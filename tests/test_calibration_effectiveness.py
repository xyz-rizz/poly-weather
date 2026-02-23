from __future__ import annotations

import json

from weather_bot.calibration_effectiveness import calibration_effectiveness_report


def _write_jsonl(path, rows):
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def test_calibration_effectiveness_report_basic(tmp_path):
    feature_rows = [
        {
            "market_id": "m1",
            "city": "Atlanta",
            "status": "opportunity",
            "hours_to_end": 18,
            "model_prob_yes": 0.34,
            "label_yes": 0,
        },
        {
            "market_id": "m2",
            "city": "Atlanta",
            "status": "opportunity",
            "hours_to_end": 18,
            "model_prob_yes": 0.36,
            "label_yes": 0,
        },
        {
            "market_id": "m3",
            "city": "Atlanta",
            "status": "opportunity",
            "hours_to_end": 18,
            "model_prob_yes": 0.38,
            "label_yes": 1,
        },
    ]
    profile = {
        "schema_version": 1,
        "bin_adjustments": {
            "city=Atlanta|h=12-24h|p=0.3-0.4": {"calibrated_prob": 0.2}
        },
    }
    feature_path = tmp_path / "feature.jsonl"
    profile_path = tmp_path / "profile.json"
    _write_jsonl(feature_path, feature_rows)
    profile_path.write_text(json.dumps(profile), encoding="utf-8")

    report = calibration_effectiveness_report(feature_rows_path=feature_path, profile_path=profile_path)
    assert report["summary"]["rows_labeled"] == 3
    assert report["summary"]["profile_hit_count"] == 3
    assert report["overall"]["raw"]["n"] == 3
    assert report["overall"]["calibrated"]["n"] == 3
    assert report["by_city"]["Atlanta"]["raw"]["n"] == 3

