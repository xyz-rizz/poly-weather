from __future__ import annotations

import json
from datetime import UTC, datetime

from weather_bot.simulation.backtest import run_replay_backtest


def _write_jsonl(path, rows):
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def test_replay_backtest_reports_unrealized_open_position(tmp_path):
    scan_time = datetime(2026, 2, 23, 12, 0, tzinfo=UTC).isoformat()
    target_time = datetime(2026, 2, 24, 5, 0, tzinfo=UTC).isoformat()

    rows = [
        {
            "event_type": "scan_snapshot",
            "scan_mode": "live_scan",
            "strategy_id": "test",
            "created_at_utc": scan_time,
            "scan_result": {
                "scanned_at_utc": scan_time,
                "evaluations": [
                    {
                        "status": "opportunity",
                        "reason": "",
                        "market": {
                            "market_id": "m1",
                            "event_slug": "e1",
                            "city": "Atlanta",
                            "target_time_utc": target_time,
                        },
                        "opportunity": {"edge": 0.2},
                        "quote": {"yes_bid": 0.30, "yes_ask": 0.34, "no_bid": 0.66, "no_ask": 0.70},
                    }
                ],
            },
        }
    ]
    _write_jsonl(tmp_path / "scan_snapshots.jsonl", rows)

    out = run_replay_backtest(tmp_path, strategy_id="test", scan_mode="live_scan", slippage_bps=0.0)

    summary = out["summary"]
    assert summary["trades"] == 0
    assert summary["open_positions"] == 1
    assert summary["gross_open_exposure_usd"] == 5.0
    assert summary["unrealized_pnl_usd"] == -0.2941
    assert summary["total_pnl_usd"] == -0.2941
    assert len(out["open_positions"]) == 1
    assert out["open_positions"][0]["market_id"] == "m1"
    assert out["open_positions"][0]["unrealized_pnl_usd"] == -0.2941

