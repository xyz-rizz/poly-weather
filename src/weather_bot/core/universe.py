from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class WeatherCityUniverseEntry:
    city: str
    station_id: str
    country: str
    unit: str
    tier: int
    rationale: str


US_TIER1_WEATHER_UNIVERSE: tuple[WeatherCityUniverseEntry, ...] = (
    WeatherCityUniverseEntry(
        city="Atlanta",
        station_id="KATL",
        country="US",
        unit="F",
        tier=1,
        rationale="Strong NWS/METAR coverage, high station reliability, clean US rule pattern",
    ),
    WeatherCityUniverseEntry(
        city="Dallas",
        station_id="KDAL",
        country="US",
        unit="F",
        tier=1,
        rationale="Strong forecast/obs availability and consistent airport-station settlement usage",
    ),
    WeatherCityUniverseEntry(
        city="NYC",
        station_id="KLGA",
        country="US",
        unit="F",
        tier=1,
        rationale="High liquidity and strong public data, but requires exact station matching",
    ),
)


US_TIER2_WEATHER_UNIVERSE: tuple[WeatherCityUniverseEntry, ...] = (
    WeatherCityUniverseEntry(
        city="Chicago",
        station_id="KORD",
        country="US",
        unit="F",
        tier=2,
        rationale="Good data and liquidity; more boundary complexity from local effects",
    ),
    WeatherCityUniverseEntry(
        city="Seattle",
        station_id="KSEA",
        country="US",
        unit="F",
        tier=2,
        rationale="Good data but marine/cloud timing can swing daily highs near bucket edges",
    ),
)


US_TIER3_WEATHER_UNIVERSE: tuple[WeatherCityUniverseEntry, ...] = (
    WeatherCityUniverseEntry(
        city="Miami",
        station_id="KMIA",
        country="US",
        unit="F",
        tier=3,
        rationale="Strong data, but sea-breeze/convection timing raises intraday uncertainty",
    ),
)


def get_universe(level: str) -> tuple[WeatherCityUniverseEntry, ...]:
    normalized = level.strip().lower()
    if normalized == "tier1":
        return US_TIER1_WEATHER_UNIVERSE
    if normalized == "tier2":
        return US_TIER1_WEATHER_UNIVERSE + US_TIER2_WEATHER_UNIVERSE
    if normalized in {"tier3", "all_us"}:
        return US_TIER1_WEATHER_UNIVERSE + US_TIER2_WEATHER_UNIVERSE + US_TIER3_WEATHER_UNIVERSE
    return US_TIER1_WEATHER_UNIVERSE
