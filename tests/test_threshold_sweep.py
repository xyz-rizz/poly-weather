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

