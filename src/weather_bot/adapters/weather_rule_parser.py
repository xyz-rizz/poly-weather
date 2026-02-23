from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Any


MONTHS = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}


CITY_ALIASES = {
    "new york city": "NYC",
    "nyc": "NYC",
    "chicago": "Chicago",
    "seattle": "Seattle",
    "atlanta": "Atlanta",
    "dallas": "Dallas",
    "miami": "Miami",
    "toronto": "Toronto",
    "london": "London",
    "seoul": "Seoul",
    "wellington": "Wellington",
    "buenos aires": "Buenos Aires",
}


@dataclass(frozen=True)
class ParsedWeatherRule:
    city: str | None
    station_id: str | None
    unit: str | None
    bucket_low: float | None
    bucket_high: float | None
    bucket_kind: str
    market_date: date | None
    settlement_source: str | None
    settlement_metric: str
    boundary_semantics: str
    timezone_name: str | None
    notes: str


def parse_weather_market_rule(row: dict[str, Any]) -> ParsedWeatherRule | None:
    text = _join_text(row)
    city = _extract_city(text) or _extract_city_from_meta(row)
    station = _extract_station(row)
    unit = _extract_unit(text) or _extract_unit_from_meta(row)
    bucket = _extract_bucket(text, unit)
    if not city or not bucket:
        return None
    market_date = _extract_market_date(text)
    settlement_source = _extract_settlement_source(row)
    timezone_name = _extract_timezone(text)
    notes = _build_notes(row)
    return ParsedWeatherRule(
        city=city,
        station_id=station,
        unit=unit,
        bucket_low=bucket[0],
        bucket_high=bucket[1],
        bucket_kind=bucket[2],
        market_date=market_date,
        settlement_source=settlement_source,
        settlement_metric="highest_temperature",
        boundary_semantics=bucket[3],
        timezone_name=timezone_name,
        notes=notes,
    )


def _join_text(row: dict[str, Any]) -> str:
    return " ".join(str(v) for v in (row.get("question"), row.get("title"), row.get("description"), row.get("resolutionCriteria")) if v)


def _extract_city(text: str) -> str | None:
    lower = text.lower()
    # prioritize longer names first
    for raw in sorted(CITY_ALIASES.keys(), key=len, reverse=True):
        if re.search(rf"\b{re.escape(raw)}\b", lower):
            return CITY_ALIASES[raw]
    return None


def _extract_city_from_meta(row: dict[str, Any]) -> str | None:
    meta = row.get("metadata")
    if not isinstance(meta, dict):
        return None
    for key in ("city", "location", "marketCity"):
        value = meta.get(key)
        if not isinstance(value, str):
            continue
        low = value.strip().lower()
        if low in CITY_ALIASES:
            return CITY_ALIASES[low]
        return value.strip()
    return None


def _extract_station(row: dict[str, Any]) -> str | None:
    text = _join_text(row)
    match = re.search(r"\b([A-Z]{4})\b", text)
    if match:
        return match.group(1)
    meta = row.get("metadata")
    if isinstance(meta, dict):
        for key in ("station", "stationId", "weatherStation"):
            value = meta.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip().upper()
    return None


def _extract_unit(text: str) -> str | None:
    if re.search(r"°?\s*F\b", text, flags=re.IGNORECASE):
        return "F"
    if re.search(r"°?\s*C\b", text, flags=re.IGNORECASE):
        return "C"
    return None


def _extract_unit_from_meta(row: dict[str, Any]) -> str | None:
    meta = row.get("metadata")
    if isinstance(meta, dict):
        for key in ("unit", "temperatureUnit"):
            value = meta.get(key)
            if isinstance(value, str) and value.upper() in {"F", "C"}:
                return value.upper()
    return None


def _extract_bucket(text: str, unit: str | None) -> tuple[float, float, str, str] | None:
    u = unit or "[FC]?"
    n = r"-?\d{1,3}"
    patterns: list[tuple[str, str, str]] = [
        (rf"({n})\s*[°]?\s*{u}\s*or below", "upper_tail", "range_upper_inclusive"),
        (rf"({n})\s*[°]?\s*{u}\s*or higher", "lower_tail", "range_lower_inclusive"),
        (rf"({n})\s*[-–]\s*({n})\s*[°]?\s*{u}", "range", "range_bucket_unknown_inclusivity"),
        (rf"between\s+({n})\s+and\s+({n})\s*[°]?\s*{u}", "range", "range_bucket_unknown_inclusivity"),
    ]
    for pattern, kind, semantics in patterns:
        m = re.search(pattern, text, flags=re.IGNORECASE)
        if not m:
            continue
        if kind == "upper_tail":
            x = float(m.group(1))
            return (-999.0, x, kind, semantics)
        if kind == "lower_tail":
            x = float(m.group(1))
            return (x, 999.0, kind, semantics)
        a = float(m.group(1))
        b = float(m.group(2))
        return (min(a, b), max(a, b), kind, semantics)
    return None


def _extract_market_date(text: str) -> date | None:
    # "on February 23?" or "... on Feb 23, 2026?"
    m = re.search(r"\bon\s+([A-Za-z]+)\s+(\d{1,2})(?:st|nd|rd|th)?(?:,?\s*(\d{4}))?", text, flags=re.IGNORECASE)
    if not m:
        return None
    month_name = m.group(1).lower()
    month = MONTHS.get(month_name)
    if month is None:
        # support abbreviated months
        for k, v in MONTHS.items():
            if k.startswith(month_name[:3]):
                month = v
                break
    if month is None:
        return None
    day = int(m.group(2))
    year = int(m.group(3)) if m.group(3) else datetime_now_year_fallback()
    try:
        return date(year, month, day)
    except ValueError:
        return None


def datetime_now_year_fallback() -> int:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).year


def _extract_settlement_source(row: dict[str, Any]) -> str | None:
    text = _join_text(row).lower()
    if "wunderground" in text:
        return "Wunderground"
    if "noaa" in text or "nws" in text:
        return "NOAA/NWS"
    if "weather station" in text:
        return "Weather Station"
    return None


def _extract_timezone(text: str) -> str | None:
    for token in ("ET", "EST", "EDT", "CT", "CST", "CDT", "PT", "PST", "PDT", "UTC"):
        if re.search(rf"\b{token}\b", text):
            return token
    return None


def _build_notes(row: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in ("resolutionCriteria", "description"):
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            parts.append(f"{key}={value.strip()[:300]}")
    return " | ".join(parts)
