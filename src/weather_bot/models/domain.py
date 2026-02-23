from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class ForecastPoint:
    source: str
    station_id: str
    city: str
    target_time_utc: datetime
    expected_temp_f: float
    low_f: float | None = None
    high_f: float | None = None
    confidence: float | None = None
    updated_at_utc: datetime | None = None


@dataclass(frozen=True)
class ForecastSnapshot:
    points: list[ForecastPoint]
    created_at_utc: datetime


@dataclass(frozen=True)
class ObservationSnapshot:
    station_id: str
    city: str
    observed_at_utc: datetime
    temp_f: float
    dewpoint_f: float | None = None
    wind_mph: float | None = None
    condition: str | None = None
    source: str = "unknown"


@dataclass(frozen=True)
class WeatherMarket:
    market_id: str
    city: str
    station_id: str
    target_time_utc: datetime
    bucket_low_f: float
    bucket_high_f: float
    event_slug: str = ""
    market_date_local: str = ""
    settlement_source: str = "unknown"
    settlement_metric: str = "temperature"
    boundary_semantics: str = "unknown"
    timezone_name: str = "UTC"
    resolution_notes: str = ""


@dataclass(frozen=True)
class MarketQuote:
    market_id: str
    yes_bid: float
    yes_ask: float
    no_bid: float
    no_ask: float
    depth_yes_top: float
    depth_no_top: float
    last_price_yes: float | None
    as_of_utc: datetime


@dataclass
class Opportunity:
    market: WeatherMarket
    quote: MarketQuote
    implied_yes_mid: float
    model_prob_yes: float
    edge: float
    confidence_score: float
    liquidity_score: float
    uncertainty_score: float
    reasons: list[str] = field(default_factory=list)
