from __future__ import annotations

import json
from datetime import UTC, datetime

from weather_bot.core.calibration_profile import apply_runtime_probability_calibration
from weather_bot.models.domain import WeatherMarket


def _market() -> WeatherMarket:
    return WeatherMarket(
        market_id="m1",
        city="Atlanta",
        station_id="KATL",
        target_time_utc=datetime(2026, 2, 24, 12, 0, tzinfo=UTC),
        bucket_low_f=60,
        bucket_high_f=61,
        event_slug="e1",
        market_date_local="2026-02-24",
        settlement_source="wunderground",
        settlement_metric="highest_temperature",
        boundary_semantics="inclusive",
        timezone_name="ET",
        resolution_notes="",
    )


def test_runtime_probability_calibration_applies_profile(monkeypatch, tmp_path):
    profile = {
        "schema_version": 1,
        "bin_adjustments": {
            "city=Atlanta|h=12-24h|p=0.3-0.4": {"calibrated_prob": 0.27}
        },
    }
    path = tmp_path / "profile.json"
    path.write_text(json.dumps(profile), encoding="utf-8")
    monkeypatch.setenv("WEATHER_BOT_CAL_PROFILE_PATH", str(path))

    out = apply_runtime_probability_calibration(
        0.34,
        market=_market(),
        now_utc=datetime(2026, 2, 23, 12, 0, tzinfo=UTC),
    )
    assert out.profile_hit is True
    assert out.calibrated_prob == 0.27

