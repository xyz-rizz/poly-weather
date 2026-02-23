from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from weather_bot.models.domain import WeatherMarket


@dataclass(frozen=True)
class CalibrationAdjustment:
    raw_prob: float
    calibrated_prob: float
    profile_hit: bool
    profile_key: str | None


def calibrate_probability_from_profile(
    profile: dict[str, Any] | None,
    *,
    raw_prob: float,
    city: str,
    hours_to_end: float | None,
) -> CalibrationAdjustment:
    if not profile or not isinstance(profile, dict):
        return CalibrationAdjustment(raw_prob, raw_prob, False, None)
    city_norm = (city or "").strip() or "unknown"
    horizon_bucket = _horizon_bucket_from_hours(hours_to_end)
    raw_bucket = _raw_prob_bucket(raw_prob)
    key_candidates = [
        f"city={city_norm}|h={horizon_bucket}|p={raw_bucket}",
        f"city={city_norm}|h=all|p={raw_bucket}",
        f"city=all|h={horizon_bucket}|p={raw_bucket}",
        f"city=all|h=all|p={raw_bucket}",
    ]
    for key in key_candidates:
        entry = (profile.get("bin_adjustments") or {}).get(key)
        if not isinstance(entry, dict):
            continue
        try:
            calibrated = float(entry.get("calibrated_prob"))
        except (TypeError, ValueError):
            continue
        calibrated = max(0.001, min(0.999, calibrated))
        return CalibrationAdjustment(raw_prob, calibrated, True, key)
    return CalibrationAdjustment(raw_prob, raw_prob, False, None)


def apply_runtime_probability_calibration(
    raw_prob: float,
    *,
    market: WeatherMarket,
    now_utc: datetime,
) -> CalibrationAdjustment:
    profile_path = os.getenv("WEATHER_BOT_CAL_PROFILE_PATH", "").strip()
    if not profile_path:
        return CalibrationAdjustment(raw_prob, raw_prob, False, None)
    profile = _load_profile(Path(profile_path))
    if not profile:
        return CalibrationAdjustment(raw_prob, raw_prob, False, None)
    hours_to_end = None
    try:
        hours_to_end = max(0.0, (market.target_time_utc.astimezone(UTC) - now_utc.astimezone(UTC)).total_seconds() / 3600.0)
    except Exception:
        hours_to_end = None
    return calibrate_probability_from_profile(
        profile,
        raw_prob=raw_prob,
        city=market.city,
        hours_to_end=hours_to_end,
    )


def _horizon_bucket(now_utc: datetime, target_time_utc: datetime) -> str:
    try:
        hours = max(0.0, (target_time_utc.astimezone(UTC) - now_utc.astimezone(UTC)).total_seconds() / 3600.0)
    except Exception:
        return "unknown"
    return _horizon_bucket_from_hours(hours)


def _horizon_bucket_from_hours(hours: float | None) -> str:
    if hours is None:
        return "unknown"
    if hours <= 2:
        return "0-2h"
    if hours <= 6:
        return "2-6h"
    if hours <= 12:
        return "6-12h"
    if hours <= 24:
        return "12-24h"
    return "24h+"


def _raw_prob_bucket(p: float) -> str:
    lo = int(max(0, min(9, p * 10)))
    hi = lo + 1
    if p >= 0.999:
        lo, hi = 9, 10
    return f"{lo/10:.1f}-{hi/10:.1f}"


_PROFILE_CACHE: dict[str, tuple[float, dict[str, Any] | None]] = {}


def _load_profile(path: Path) -> dict[str, Any] | None:
    key = str(path)
    try:
        mtime = path.stat().st_mtime
    except Exception:
        _PROFILE_CACHE.pop(key, None)
        return None
    cached = _PROFILE_CACHE.get(key)
    if cached and cached[0] == mtime:
        return cached[1]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        _PROFILE_CACHE[key] = (mtime, None)
        return None
    parsed = data if isinstance(data, dict) else None
    _PROFILE_CACHE[key] = (mtime, parsed)
    return parsed
