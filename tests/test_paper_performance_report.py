from __future__ import annotations

import json

from weather_bot.paper_performance_report import build_paper_performance_report


def test_paper_performance_report_groups_closed_and_open(tmp_path):
    base = tmp_path
    data = base
    ledger = data / "paper_settlement_ledger.jsonl"
    state = data / "paper_settlement_state.json"
    features = data / "feature_rows_export.jsonl"

    ledger_rows = [
        {
            "event_type": "paper_settlement_trade",
            "created_at_utc": "2026-02-24T10:00:00Z",
            "market_id": "m1",
            "city": "Atlanta",
            "direction": "BUY_NO",
            "pnl_usd": 1.2,
            "return_pct": 0.24,
            "signal_time_utc": "2026-02-23T18:00:00Z",
            "target_time_utc": "2026-02-24T00:00:00Z",
        },
        {
            "event_type": "paper_mark_exit_trade",
            "created_at_utc": "2026-02-24T11:00:00Z",
            "market_id": "m2",
            "city": "Dallas",
            "direction": "BUY_YES",
            "pnl_usd": -0.5,
            "return_pct": -0.1,
            "mark_reason": "stop_loss",
            "exit_regime_key": "city=Dallas|h=12-24h",
            "exit_regime_horizon": "12-24h",
            "signal_time_utc": "2026-02-24T06:00:00Z",
            "target_time_utc": "2026-02-24T18:00:00Z",
        },
    ]
    ledger.write_text("".join(json.dumps(r) + "\n" for r in ledger_rows), encoding="utf-8")

    state.write_text(
        json.dumps(
            {
                "realized_pnl_usd": 0.7,
                "open_positions": [
                    {
                        "market_id": "m3",
                        "event_slug": "e3",
                        "city": "NYC",
                        "direction": "BUY_YES",
                        "size_usd": 5.0,
                        "entry_price": 0.25,
                        "signal_time_utc": "2026-02-24T08:00:00Z",
                        "target_time_utc": "2026-02-24T20:00:00Z",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    features.write_text(
        json.dumps(
            {
                "market_id": "m3",
                "implied_yes_mid": 0.30,
                "quote_time_utc": "2026-02-24T12:00:00Z",
                "snapshot_time_utc": "2026-02-24T12:00:00Z",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    out = build_paper_performance_report(base_dir=base)
    assert out["closed_summary"]["trades"] == 2
    assert out["closed_summary"]["wins"] == 1
    assert out["closed_counts"]["exit_reasons"]["settlement"] == 1
    assert out["closed_counts"]["exit_reasons"]["stop_loss"] == 1
    assert "by_exit_regime" in out["closed_breakdowns"]
    assert "tuning_candidates" in out
    assert out["open_summary"]["open_positions"] == 1
    assert out["open_summary"]["marked_open_positions"] == 1
    assert out["open_summary"]["unrealized_pnl_usd"] > 0
