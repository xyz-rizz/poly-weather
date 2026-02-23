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
        if y is None or edge is None or conf is None:
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
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

