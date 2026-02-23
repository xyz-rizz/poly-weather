from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime

from weather_bot.models.domain import (
    ForecastSnapshot,
    ObservationSnapshot,
    MarketQuote,
    WeatherMarket,
)


class ForecastSource(ABC):
    name: str

    @abstractmethod
    def fetch_forecasts(self, cities: list[str], target_time_utc: datetime) -> ForecastSnapshot:
        raise NotImplementedError


class ObservationSource(ABC):
    name: str

    @abstractmethod
    def fetch_latest(self, city: str, station_id: str) -> ObservationSnapshot:
        raise NotImplementedError


class MarketSource(ABC):
    name: str

    @abstractmethod
    def list_markets(self) -> list[WeatherMarket]:
        raise NotImplementedError

    @abstractmethod
    def fetch_quote(self, market_id: str) -> MarketQuote:
        raise NotImplementedError
