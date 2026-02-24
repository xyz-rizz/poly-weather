from __future__ import annotations

import json
import os
from collections import Counter, defaultdict
from datetime import UTC, datetime
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


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
        return obj if isinstance(obj, dict) else {}
    except Exception:
        return {}


def _parse_dt(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    txt = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        return datetime.fromisoformat(txt).astimezone(UTC)
    except ValueError:
        return None


def _f(value: Any) -> float | None:
    try:
        return None if value is None else float(value)
    except Exception:
        return None


def _horizon_bucket(hours_to_end: float | None) -> str:
    if hours_to_end is None:
        return "unknown"
    if hours_to_end < 0:
        return "expired"
    if hours_to_end < 2:
        return "<2h"
    if hours_to_end < 6:
        return "2-6h"
    if hours_to_end < 12:
        return "6-12h"
    if hours_to_end < 24:
        return "12-24h"
    return "24h+"


def _bucket_metrics(rows: list[dict[str, Any]], *, pnl_key: str = "pnl_usd", ret_key: str = "return_pct") -> dict[str, Any]:
    pnls = [_f(r.get(pnl_key)) for r in rows]
    rets = [_f(r.get(ret_key)) for r in rows]
    pnls = [x for x in pnls if x is not None]
    rets = [x for x in rets if x is not None]
    wins = sum(1 for x in pnls if x > 0)
    losses = sum(1 for x in pnls if x < 0)
    flat = sum(1 for x in pnls if x == 0)
    total_pnl = sum(pnls)
    n = len(rows)
    avg_ret = (sum(rets) / len(rets)) if rets else None
    avg_pnl = (total_pnl / len(pnls)) if pnls else None
    return {
        "trades": n,
        "wins": wins,
        "losses": losses,
        "flat": flat,
        "win_rate": None if n == 0 else round(wins / n, 6),
        "total_pnl_usd": round(total_pnl, 6),
        "avg_pnl_usd": None if avg_pnl is None else round(avg_pnl, 6),
        "avg_return_pct": None if avg_ret is None else round(avg_ret, 6),
        "profit_factor": _profit_factor(pnls),
    }


def _profit_factor(pnls: list[float]) -> float | None:
    gross_profit = sum(x for x in pnls if x > 0)
    gross_loss = abs(sum(x for x in pnls if x < 0))
    if gross_loss == 0:
        if gross_profit > 0:
            return None
        return None
    return round(gross_profit / gross_loss, 6)


def _group_metrics(rows: list[dict[str, Any]], key_fn, *, pnl_key: str = "pnl_usd", ret_key: str = "return_pct") -> dict[str, Any]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[str(key_fn(row) or "unknown")].append(row)
    return {k: _bucket_metrics(v, pnl_key=pnl_key, ret_key=ret_key) for k, v in sorted(groups.items(), key=lambda kv: kv[0])}


def _latest_mark_rows(base_dir: Path) -> tuple[dict[str, dict[str, Any]], str | None]:
    feature_path = Path(os.getenv("WEATHER_BOT_FEATURE_EXPORT_PATH", str(base_dir / "feature_rows_export.jsonl")))
    rows = _read_jsonl(feature_path)
    if not rows:
        return {}, None
    latest: dict[str, dict[str, Any]] = {}
    latest_ts: dict[str, datetime] = {}
    for row in rows:
        market_id = str(row.get("market_id") or "")
        if not market_id:
            continue
        ts = _parse_dt(row.get("quote_time_utc")) or _parse_dt(row.get("snapshot_time_utc"))
        if ts is None:
            continue
        prev = latest_ts.get(market_id)
        if prev is None or ts > prev:
            latest_ts[market_id] = ts
            latest[market_id] = row
    return latest, str(feature_path)


def build_paper_performance_report(*, base_dir: Path, out_path: Path | None = None) -> dict[str, Any]:
    ledger_path = Path(os.getenv("WEATHER_BOT_PAPER_SETTLEMENT_LEDGER", str(base_dir / "paper_settlement_ledger.jsonl")))
    state_path = Path(os.getenv("WEATHER_BOT_PAPER_SETTLEMENT_STATE", str(base_dir / "paper_settlement_state.json")))
    rows = [
        r
        for r in _read_jsonl(ledger_path)
        if r.get("event_type") in {"paper_settlement_trade", "paper_mark_exit_trade"}
    ]
    rows.sort(key=lambda r: str(r.get("created_at_utc") or ""))

    closed_rows: list[dict[str, Any]] = []
    for r in rows:
        signal_dt = _parse_dt(r.get("signal_time_utc"))
        target_dt = _parse_dt(r.get("target_time_utc"))
        exit_dt = _parse_dt(r.get("created_at_utc"))
        hours_to_end_at_entry = None
        if signal_dt is not None and target_dt is not None:
            hours_to_end_at_entry = (target_dt - signal_dt).total_seconds() / 3600.0
        hold_hours = None
        if signal_dt is not None and exit_dt is not None:
            hold_hours = (exit_dt - signal_dt).total_seconds() / 3600.0
        rr = dict(r)
        rr["exit_reason"] = str(r.get("mark_reason") or ("settlement" if r.get("event_type") == "paper_settlement_trade" else "unknown"))
        rr["hours_to_end_at_entry"] = None if hours_to_end_at_entry is None else round(hours_to_end_at_entry, 6)
        rr["entry_horizon_bucket"] = _horizon_bucket(hours_to_end_at_entry)
        rr["hold_hours"] = None if hold_hours is None else round(hold_hours, 6)
        closed_rows.append(rr)

    overall = _bucket_metrics(closed_rows)
    exit_reason_counts = Counter(r.get("exit_reason") or "unknown" for r in closed_rows)
    event_type_counts = Counter(str(r.get("event_type") or "unknown") for r in closed_rows)

    state = _read_json(state_path)
    open_positions_raw = state.get("open_positions") or []
    latest_marks, mark_source_path = _latest_mark_rows(base_dir)
    now_utc = datetime.now(UTC)
    open_rows: list[dict[str, Any]] = []
    marked_open = 0
    unrealized_total = 0.0
    gross_open_exposure = 0.0
    for row in open_positions_raw:
        if not isinstance(row, dict):
            continue
        market_id = str(row.get("market_id") or "")
        direction = str(row.get("direction") or "BUY_YES")
        entry_price = _f(row.get("entry_price"))
        size_usd = _f(row.get("size_usd"))
        signal_dt = _parse_dt(row.get("signal_time_utc"))
        target_dt = _parse_dt(row.get("target_time_utc"))
        if not market_id or entry_price is None or size_usd is None:
            continue
        gross_open_exposure += size_usd
        rec: dict[str, Any] = {
            "market_id": market_id,
            "event_slug": row.get("event_slug"),
            "city": row.get("city"),
            "direction": direction,
            "size_usd": size_usd,
            "entry_price": entry_price,
            "signal_time_utc": row.get("signal_time_utc"),
            "target_time_utc": row.get("target_time_utc"),
            "entry_horizon_bucket": _horizon_bucket(
                None if signal_dt is None or target_dt is None else (target_dt - signal_dt).total_seconds() / 3600.0
            ),
            "hours_to_target_now": None if target_dt is None else round((target_dt - now_utc).total_seconds() / 3600.0, 6),
        }
        mark = latest_marks.get(market_id)
        if isinstance(mark, dict):
            if rec.get("target_time_utc") is None and mark.get("target_time_utc"):
                rec["target_time_utc"] = mark.get("target_time_utc")
                target_dt = _parse_dt(rec.get("target_time_utc"))
                rec["hours_to_target_now"] = None if target_dt is None else round((target_dt - now_utc).total_seconds() / 3600.0, 6)
                if signal_dt is not None and target_dt is not None:
                    rec["entry_horizon_bucket"] = _horizon_bucket((target_dt - signal_dt).total_seconds() / 3600.0)
            mark_yes = _f(mark.get("implied_yes_mid"))
            if mark_yes is None:
                yb = _f(mark.get("yes_bid"))
                ya = _f(mark.get("yes_ask"))
                if yb is not None and ya is not None:
                    mark_yes = (yb + ya) / 2.0
            if mark_yes is not None:
                mark_yes = max(0.001, min(0.999, mark_yes))
                mark_pos = mark_yes if direction == "BUY_YES" else (1.0 - mark_yes)
                shares = size_usd / max(entry_price, 1e-9)
                unrealized = shares * (mark_pos - entry_price)
                ret = (mark_pos - entry_price) / max(entry_price, 1e-9)
                unrealized_total += unrealized
                marked_open += 1
                rec.update(
                    {
                        "mark_yes_mid": round(mark_yes, 6),
                        "mark_quote_time_utc": mark.get("quote_time_utc") or mark.get("snapshot_time_utc"),
                        "mark_position_price": round(mark_pos, 6),
                        "unrealized_pnl_usd": round(unrealized, 6),
                        "unrealized_return_pct": round(ret, 6),
                    }
                )
        open_rows.append(rec)

    report = {
        "as_of_utc": now_utc.isoformat(),
        "ledger_path": str(ledger_path),
        "state_path": str(state_path),
        "mark_source_path": mark_source_path,
        "closed_summary": overall,
        "closed_breakdowns": {
            "by_exit_reason": _group_metrics(closed_rows, lambda r: r.get("exit_reason")),
            "by_city": _group_metrics(closed_rows, lambda r: r.get("city")),
            "by_direction": _group_metrics(closed_rows, lambda r: r.get("direction")),
            "by_entry_horizon": _group_metrics(closed_rows, lambda r: r.get("entry_horizon_bucket")),
        },
        "closed_counts": {
            "event_types": dict(event_type_counts),
            "exit_reasons": dict(exit_reason_counts),
        },
        "open_summary": {
            "open_positions": len(open_rows),
            "marked_open_positions": marked_open,
            "unmarked_open_positions": len(open_rows) - marked_open,
            "gross_open_exposure_usd": round(gross_open_exposure, 6),
            "unrealized_pnl_usd": round(unrealized_total, 6),
            "total_pnl_including_open_usd": round((_f(state.get("realized_pnl_usd")) or 0.0) + unrealized_total, 6),
            "realized_pnl_from_state_usd": round(_f(state.get("realized_pnl_usd")) or 0.0, 6),
        },
        "open_breakdowns": {
            "by_city": _group_metrics(
                [r for r in open_rows if r.get("unrealized_pnl_usd") is not None],
                lambda r: r.get("city"),
                pnl_key="unrealized_pnl_usd",
                ret_key="unrealized_return_pct",
            ),
            "by_direction": _group_metrics(
                [r for r in open_rows if r.get("unrealized_pnl_usd") is not None],
                lambda r: r.get("direction"),
                pnl_key="unrealized_pnl_usd",
                ret_key="unrealized_return_pct",
            ),
            "by_entry_horizon": _group_metrics(
                [r for r in open_rows if r.get("unrealized_pnl_usd") is not None],
                lambda r: r.get("entry_horizon_bucket"),
                pnl_key="unrealized_pnl_usd",
                ret_key="unrealized_return_pct",
            ),
        },
        "open_positions_sample": open_rows[:25],
    }
    if out_path is not None:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    return report


def run_paper_performance_report() -> dict[str, Any]:
    base_dir = Path(os.getenv("WEATHER_BOT_RUNNER_BASEDIR", "data/sample"))
    out_path = Path(os.getenv("WEATHER_BOT_PAPER_PERFORMANCE_REPORT", str(base_dir / "paper_performance_report.json")))
    report = build_paper_performance_report(base_dir=base_dir, out_path=out_path)
    report["report_path"] = str(out_path)
    return report


def main() -> int:
    print(json.dumps(run_paper_performance_report(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
