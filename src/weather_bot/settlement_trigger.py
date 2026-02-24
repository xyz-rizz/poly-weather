from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from weather_bot.calibration_refresh import run_calibration_refresh
from weather_bot.feature_export import export_feature_rows
from weather_bot.paper_performance_report import run_paper_performance_report
from weather_bot.paper_settlement_reconcile import run_paper_settlement_reconcile


def _iso_now() -> str:
    return datetime.now(UTC).isoformat()


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")


def run_settlement_trigger() -> dict[str, Any]:
    base_dir = Path(os.getenv("WEATHER_BOT_RUNNER_BASEDIR", "data/sample"))
    state_path = Path(os.getenv("WEATHER_BOT_SETTLEMENT_TRIGGER_STATE", str(base_dir / "settlement_trigger_state.json")))
    min_new_matches = int(os.getenv("WEATHER_BOT_SETTLEMENT_MIN_NEW_MATCHES", "1"))
    force = os.getenv("WEATHER_BOT_SETTLEMENT_FORCE", "0").strip().lower() in {"1", "true", "yes"}
    attach_outcomes = True

    export_result = export_feature_rows(
        base_dir=base_dir,
        out_path=Path(os.getenv("WEATHER_BOT_FEATURE_EXPORT_PATH", str(base_dir / "feature_rows_export.jsonl"))),
        strategy_id=os.getenv("WEATHER_BOT_STRATEGY_ID") or None,
        scan_mode=os.getenv("WEATHER_BOT_SCAN_MODE") or None,
        latest_only=os.getenv("WEATHER_BOT_FEATURE_LATEST_ONLY", "0").strip().lower() in {"1", "true", "yes"},
        attach_outcomes=attach_outcomes,
        universe_level=os.getenv("WEATHER_BOT_UNIVERSE", "tier1"),
        days_back=int(os.getenv("WEATHER_BOT_CAL_DAYS_BACK", "30")),
        insecure_ssl=os.getenv("WEATHER_BOT_INSECURE_SSL", "0").strip().lower() in {"1", "true", "yes"},
    )

    prev = _load_json(state_path)
    prev_matched = int(prev.get("matched_market_ids") or 0)
    prev_rows_with_outcome = int(prev.get("rows_with_outcome") or 0)
    cur_matched = int(export_result.get("matched_market_ids") or 0)
    cur_rows_with_outcome = int(export_result.get("rows_with_outcome") or 0)

    new_matched = max(0, cur_matched - prev_matched)
    new_labeled_rows = max(0, cur_rows_with_outcome - prev_rows_with_outcome)

    should_trigger = force or (new_matched >= min_new_matches)
    refresh_result: dict[str, Any] | None = None
    paper_settlement_result: dict[str, Any] | None = None
    paper_performance_result: dict[str, Any] | None = None
    action = "skip"
    reason = "no new matched settled markets"
    if should_trigger:
        action = "refresh"
        reason = "forced" if force else f"new matched settled markets: +{new_matched}"
        refresh_result = run_calibration_refresh()
    paper_settlement_result = run_paper_settlement_reconcile()
    paper_performance_result = run_paper_performance_report()

    next_state = {
        "last_checked_utc": _iso_now(),
        "last_action": action,
        "last_reason": reason,
        "matched_market_ids": cur_matched,
        "rows_with_outcome": cur_rows_with_outcome,
        "distinct_markets": int(export_result.get("distinct_markets") or 0),
        "resolved_market_ids_available": int(export_result.get("resolved_market_ids_available") or 0),
        "snapshot_time_min": export_result.get("snapshot_time_min"),
        "snapshot_time_max": export_result.get("snapshot_time_max"),
        "target_time_min": export_result.get("target_time_min"),
        "target_time_max": export_result.get("target_time_max"),
    }
    if refresh_result is not None:
        next_state["last_refresh_utc"] = _iso_now()
        next_state["last_refresh_export"] = refresh_result.get("export")
        next_state["last_refresh_effectiveness_summary"] = refresh_result.get("effectiveness_summary")
        next_state["last_refresh_walkforward_summary"] = refresh_result.get("walkforward_summary")
    if paper_settlement_result is not None:
        next_state["last_paper_settlement_utc"] = _iso_now()
        next_state["last_paper_settlement_summary"] = {
            k: paper_settlement_result.get(k)
            for k in (
                "open_positions",
                "closed_this_run",
                "settled_this_run",
                "mark_exits_this_run",
                "partial_mark_exits_this_run",
                "metadata_backfills_this_run",
                "realized_pnl_delta_usd",
                "realized_pnl_total_usd",
                "settled_trades_total",
            )
        }
    if paper_performance_result is not None:
        next_state["last_paper_performance_utc"] = _iso_now()
        next_state["last_paper_performance_summary"] = {
            "closed_trades": (((paper_performance_result.get("closed_summary") or {}).get("trades"))),
            "closed_total_pnl_usd": (((paper_performance_result.get("closed_summary") or {}).get("total_pnl_usd"))),
            "open_positions": (((paper_performance_result.get("open_summary") or {}).get("open_positions"))),
            "open_unrealized_pnl_usd": (((paper_performance_result.get("open_summary") or {}).get("unrealized_pnl_usd"))),
            "total_pnl_including_open_usd": (((paper_performance_result.get("open_summary") or {}).get("total_pnl_including_open_usd"))),
        }
    _write_json(state_path, next_state)

    return {
        "action": action,
        "reason": reason,
        "new_matched_market_ids": new_matched,
        "new_labeled_rows": new_labeled_rows,
        "previous": {
            "matched_market_ids": prev_matched,
            "rows_with_outcome": prev_rows_with_outcome,
        },
        "current": {
            "matched_market_ids": cur_matched,
            "rows_with_outcome": cur_rows_with_outcome,
            "distinct_markets": int(export_result.get("distinct_markets") or 0),
            "resolved_market_ids_available": int(export_result.get("resolved_market_ids_available") or 0),
            "snapshot_time_min": export_result.get("snapshot_time_min"),
            "snapshot_time_max": export_result.get("snapshot_time_max"),
            "target_time_min": export_result.get("target_time_min"),
            "target_time_max": export_result.get("target_time_max"),
        },
        "refresh_result": refresh_result,
        "paper_settlement_result": paper_settlement_result,
        "paper_performance_result": paper_performance_result,
        "state_path": str(state_path),
    }


def main() -> int:
    print(json.dumps(run_settlement_trigger(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
