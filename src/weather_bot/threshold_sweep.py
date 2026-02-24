from __future__ import annotations

import json
import math
import os
from pathlib import Path
from typing import Any


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            out.append(obj)
    return out


def _f(v: Any) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _i(v: Any) -> int | None:
    try:
        i = int(v)
    except (TypeError, ValueError):
        return None
    return i if i in (0, 1) else None


def _round(v: float | None) -> float | None:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return None
    return round(v, 6)


def _ts_key(row: dict[str, Any]) -> tuple[int, str]:
    # Stable ordering for "first actionable snapshot" selection.
    for key in ("snapshot_time_utc", "quote_time_utc", "created_at_utc"):
        val = row.get(key)
        if isinstance(val, str) and val:
            return (0, val)
    return (1, str(row.get("market_id") or ""))


def _conservative_resolution_trade_proxy(row: dict[str, Any], *, stake_usd: float) -> dict[str, float] | None:
    edge = _f(row.get("edge"))
    label_yes = _i(row.get("label_yes"))
    if edge is None or label_yes is None:
        return None

    yes_ask = _f(row.get("yes_ask"))
    no_ask = _f(row.get("no_ask"))
    yes_bid = _f(row.get("yes_bid"))
    no_bid = _f(row.get("no_bid"))
    # Conservative fallback: derive no side from yes quote if direct quote absent.
    if no_ask is None and yes_bid is not None:
        no_ask = 1.0 - yes_bid
    if no_bid is None and yes_ask is not None:
        no_bid = 1.0 - yes_ask

    if edge > 0:
        direction = "BUY_YES"
        entry = yes_ask
        outcome_price = 1.0 if label_yes == 1 else 0.0
    else:
        direction = "BUY_NO"
        entry = no_ask
        outcome_price = 1.0 if label_yes == 0 else 0.0
    if entry is None or not (0.001 <= entry <= 0.999):
        return None

    shares = stake_usd / entry
    pnl = shares * (outcome_price - entry)
    return {
        "direction": 1.0 if direction == "BUY_YES" else -1.0,
        "entry_price": entry,
        "pnl_usd": pnl,
        "return_pct": (outcome_price - entry) / entry,
    }


def threshold_sweep_report(
    *,
    feature_rows_path: Path,
    out_path: Path | None = None,
    stake_usd: float = 5.0,
    max_spread: float = 0.18,
    statuses: set[str] | None = None,
    dedupe_market: bool = True,
    max_positions_per_event: int = 1,
    min_hours_to_end: float = 0.0,
) -> dict[str, Any]:
    rows = _read_jsonl(feature_rows_path)
    candidates: list[dict[str, Any]] = []
    for r in rows:
        if statuses and str(r.get("status") or "") not in statuses:
            continue
        y = _i(r.get("label_yes"))
        edge = _f(r.get("edge"))
        conf = _f(r.get("confidence_score"))
        spread = _f(r.get("quote_spread_yes"))
        hours_to_end = _f(r.get("hours_to_end"))
        if y is None or edge is None or conf is None:
            continue
        if hours_to_end is None or hours_to_end < min_hours_to_end:
            continue
        if spread is not None and spread > max_spread:
            continue
        proxy = _conservative_resolution_trade_proxy(r, stake_usd=stake_usd)
        if proxy is None:
            continue
        row = dict(r)
        row["_proxy"] = proxy
        candidates.append(row)

    edge_grid = [0.04, 0.06, 0.08, 0.10, 0.12, 0.15]
    conf_grid = [0.45, 0.50, 0.55, 0.60, 0.65, 0.70]
    results: list[dict[str, Any]] = []
    for e_thr in edge_grid:
        for c_thr in conf_grid:
            selected = [
                r for r in candidates
                if abs(float(r.get("edge") or 0.0)) >= e_thr and float(r.get("confidence_score") or 0.0) >= c_thr
            ]
            selected.sort(key=_ts_key)
            if dedupe_market:
                seen_market_ids: set[str] = set()
                deduped: list[dict[str, Any]] = []
                for r in selected:
                    market_id = str(r.get("market_id") or "")
                    if not market_id or market_id in seen_market_ids:
                        continue
                    seen_market_ids.add(market_id)
                    deduped.append(r)
                selected = deduped
            if max_positions_per_event >= 0:
                # Resolution-proxy benchmark should not overtrade mutually exclusive buckets.
                # Keep the strongest qualifying rows per event (fallback to market_id for unknown event slug).
                grouped: dict[str, list[dict[str, Any]]] = {}
                for r in selected:
                    event_key = str(r.get("event_slug") or "") or f"market:{str(r.get('market_id') or '')}"
                    grouped.setdefault(event_key, []).append(r)
                capped: list[dict[str, Any]] = []
                for group in grouped.values():
                    group_sorted = sorted(
                        group,
                        key=lambda r: (
                            abs(float(r.get("edge") or 0.0)),
                            float(r.get("confidence_score") or 0.0),
                            -_ts_key(r)[0],
                            _ts_key(r)[1],
                        ),
                        reverse=True,
                    )
                    capped.extend(group_sorted[:max(0, max_positions_per_event)])
                selected = sorted(capped, key=_ts_key)
            if not selected:
                continue
            pnls = [float(r["_proxy"]["pnl_usd"]) for r in selected]
            rets = [float(r["_proxy"]["return_pct"]) for r in selected]
            wins = sum(1 for p in pnls if p > 0)
            losses = sum(1 for p in pnls if p < 0)
            hit = wins / len(selected)
            avg_pnl = sum(pnls) / len(pnls)
            total_pnl = sum(pnls)
            avg_ret = sum(rets) / len(rets)
            results.append(
                {
                    "min_edge": e_thr,
                    "min_confidence": c_thr,
                    "trades": len(selected),
                    "wins": wins,
                    "losses": losses,
                    "win_rate": _round(hit),
                    "avg_pnl_usd": _round(avg_pnl),
                    "total_pnl_usd": _round(total_pnl),
                    "avg_return_pct": _round(avg_ret),
                    "avg_abs_edge": _round(sum(abs(float(r["edge"])) for r in selected) / len(selected)),
                    "avg_confidence": _round(sum(float(r["confidence_score"]) for r in selected) / len(selected)),
                    "distinct_market_ids": len({str(r.get("market_id") or "") for r in selected if str(r.get("market_id") or "")}),
                    "distinct_events": len({(str(r.get("event_slug") or "") or f"market:{str(r.get('market_id') or '')}") for r in selected}),
                }
            )

    # Rank by robustness first, then profitability.
    ranked = sorted(
        results,
        key=lambda r: (
            int(r["trades"] >= 20),
            int(r["trades"] >= 10),
            float(r["avg_pnl_usd"] or 0.0),
            float(r["total_pnl_usd"] or 0.0),
        ),
        reverse=True,
    )
    report = {
        "summary": {
            "rows_total": len(rows),
            "rows_eligible": len(candidates),
            "stake_usd": stake_usd,
            "max_spread": max_spread,
            "statuses": sorted(statuses) if statuses else None,
            "grid_size": len(edge_grid) * len(conf_grid),
            "results": len(results),
            "dedupe_market": dedupe_market,
            "max_positions_per_event": max_positions_per_event,
            "min_hours_to_end": min_hours_to_end,
        },
        "top_configs": ranked[:25],
    }
    if out_path is not None:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    return report


def main() -> int:
    base_dir = Path(os.getenv("WEATHER_BOT_RUNNER_BASEDIR", "data/sample"))
    feature_rows_path = Path(os.getenv("WEATHER_BOT_FEATURE_EXPORT_PATH", str(base_dir / "feature_rows_export.jsonl")))
    out_path = Path(os.getenv("WEATHER_BOT_THRESHOLD_SWEEP_OUT", str(base_dir / "threshold_sweep_report.json")))
    statuses_env = (os.getenv("WEATHER_BOT_THRESHOLD_STATUSES") or "").strip()
    statuses = {s.strip() for s in statuses_env.split(",") if s.strip()} if statuses_env else None
    report = threshold_sweep_report(
        feature_rows_path=feature_rows_path,
        out_path=out_path,
        stake_usd=float(os.getenv("WEATHER_BOT_BT_SIZE_USD", "5")),
        max_spread=float(os.getenv("WEATHER_BOT_BT_MAX_SPREAD", "0.18")),
        statuses=statuses,
        dedupe_market=os.getenv("WEATHER_BOT_BT_DEDUPE_MARKET", "1").strip().lower() in {"1", "true", "yes"},
        max_positions_per_event=int(os.getenv("WEATHER_BOT_BT_MAX_POSITIONS_PER_EVENT", "1")),
        min_hours_to_end=float(os.getenv("WEATHER_BOT_BT_MIN_HOURS_TO_END", "0")),
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
