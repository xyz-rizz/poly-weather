from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from weather_bot.calibration_walkforward import walkforward_calibration_report


def _write_jsonl(path, rows):
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def test_walkforward_runs_folds(tmp_path):
    base = datetime(2026, 1, 1, tzinfo=UTC)
    rows = []
    for i in range(24):
        ts = (base + timedelta(hours=i)).isoformat()
        rows.append(
            {
                "market_id": f"m{i}",
                "snapshot_time_utc": ts,
                "city": "Atlanta" if i % 2 == 0 else "NYC",
                "status": "opportunity",
                "hours_to_end": 12.0,
                "model_prob_yes": 0.3 if i % 3 else 0.7,
                "label_yes": 0 if i % 4 else 1,
            }
        )
    p = tmp_path / "features.jsonl"
    _write_jsonl(p, rows)
    report = walkforward_calibration_report(
        feature_rows_path=p,
        folds=4,
        min_train_rows=4,
        min_bin_samples=2,
        shrinkage_n=5,
        restrict_status="opportunity",
    )
    assert report["summary"]["rows_labeled"] == 24
    assert report["summary"]["folds_run"] > 0
    assert "aggregate" in report
    assert isinstance(report["folds"], list)

