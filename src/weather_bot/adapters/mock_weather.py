from __future__ import annotations

from datetime import datetime, timedelta, timezone

from weather_bot.core.interfaces import ForecastSource, ObservationSource
from weather_bot.models.domain import ForecastPoint, ForecastSnapshot, ObservationSnapshot


CITY_STATIONS = {
    "NYC": "KJFK",
    "Chicago": "KORD",
    "Seattle": "KSEA",
}


class MockForecastSource(ForecastSource):
    def __init__(self, name: str, temp_bias: float = 0.0, range_width: float = 4.0) -> None:
        self.name = name
        self._bias = temp_bias
        self._range = range_width

    def fetch_forecasts(self, cities: list[str], target_time_utc: datetime) -> ForecastSnapshot:
        base_map = {
            "NYC": 74.0,
            "Chicago": 68.0,
            "Seattle": 61.5,
        }
        created = datetime.now(timezone.utc)
        points: list[ForecastPoint] = []
        for city in cities:
            expected = base_map.get(city, 70.0) + self._bias
            points.append(
                ForecastPoint(
                    source=self.name,
                    station_id=CITY_STATIONS.get(city, "XXXX"),
                    city=city,
                    target_time_utc=target_time_utc,
                    expected_temp_f=expected,
                    low_f=expected - self._range / 2,
                    high_f=expected + self._range / 2,
                    confidence=0.75,
                    updated_at_utc=created - timedelta(minutes=10),
                )
            )
        return ForecastSnapshot(points=points, created_at_utc=created)


class MockObservationSource(ObservationSource):
    name = "mock-metar"

    def fetch_latest(self, city: str, station_id: str) -> ObservationSnapshot:
        temp_map = {
            "NYC": 72.8,
            "Chicago": 66.9,
            "Seattle": 60.7,
        }
        return ObservationSnapshot(
            station_id=station_id,
            city=city,
            observed_at_utc=datetime.now(timezone.utc),
            temp_f=temp_map.get(city, 70.0),
            condition="partly cloudy",
            source=self.name,
        )
