from __future__ import annotations

import json

from weather_bot.threshold_sweep import threshold_sweep_report


def _write_jsonl(path, rows):
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def test_threshold_sweep_generates_configs(tmp_path):
    rows = [
        {
            "market_id": "m1",
            "status": "opportunity",
            "edge": 0.12,
            "confidence_score": 0.7,
            "quote_spread_yes": 0.05,
            "hours_to_end": 1.0,
            "yes_ask": 0.4,
            "yes_bid": 0.36,
            "no_ask": 0.64,
            "no_bid": 0.6,
            "label_yes": 1,
        },
        {
            "market_id": "m2",
            "status": "opportunity",
            "edge": -0.11,
            "confidence_score": 0.68,
            "quote_spread_yes": 0.04,
            "hours_to_end": 1.0,
            "yes_ask": 0.7,
            "yes_bid": 0.66,
            "no_ask": 0.34,
            "no_bid": 0.3,
            "label_yes": 0,
        },
    ]
    p = tmp_path / "features.jsonl"
    _write_jsonl(p, rows)
    report = threshold_sweep_report(feature_rows_path=p, stake_usd=5.0, max_spread=0.18)
    assert report["summary"]["rows_eligible"] == 2
    assert report["summary"]["results"] > 0
    assert len(report["top_configs"]) > 0


def test_threshold_sweep_dedupes_same_market_and_caps_event(tmp_path):
    rows = [
        {
            "market_id": "m1",
            "event_slug": "evt-1",
            "snapshot_time_utc": "2026-02-24T00:00:00Z",
            "status": "opportunity",
            "edge": 0.12,
            "confidence_score": 0.70,
            "quote_spread_yes": 0.02,
            "hours_to_end": 1.0,
            "yes_ask": 0.40,
            "yes_bid": 0.38,
            "no_ask": 0.62,
            "no_bid": 0.60,
            "label_yes": 1,
        },
        {
            # Same market at a later snapshot; should not count twice.
            "market_id": "m1",
            "event_slug": "evt-1",
            "snapshot_time_utc": "2026-02-24T00:05:00Z",
            "status": "opportunity",
            "edge": 0.20,
            "confidence_score": 0.90,
            "quote_spread_yes": 0.02,
            "hours_to_end": 0.9,
            "yes_ask": 0.42,
            "yes_bid": 0.40,
            "no_ask": 0.60,
            "no_bid": 0.58,
            "label_yes": 1,
        },
        {
            # Different bucket, same event. Event cap should keep only one.
            "market_id": "m2",
            "event_slug": "evt-1",
            "snapshot_time_utc": "2026-02-24T00:03:00Z",
            "status": "opportunity",
            "edge": -0.18,
            "confidence_score": 0.85,
            "quote_spread_yes": 0.03,
            "hours_to_end": 0.95,
            "yes_ask": 0.72,
            "yes_bid": 0.69,
            "no_ask": 0.31,
            "no_bid": 0.28,
            "label_yes": 0,
        },
    ]
    p = tmp_path / "features.jsonl"
    _write_jsonl(p, rows)
    report = threshold_sweep_report(feature_rows_path=p, stake_usd=5.0, max_spread=0.18)
    top = report["top_configs"][0]
    assert report["summary"]["dedupe_market"] is True
    assert report["summary"]["max_positions_per_event"] == 1
    assert top["distinct_market_ids"] == 1
    assert top["distinct_events"] == 1
