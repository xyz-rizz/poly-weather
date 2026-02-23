from datetime import datetime, timezone

from weather_bot.core.config import ScanConfig
from weather_bot.core.scoring import build_opportunity
from weather_bot.models.domain import ForecastPoint, MarketQuote, ObservationSnapshot, WeatherMarket


def test_build_opportunity_outputs_bounded_scores() -> None:
    target = datetime(2026, 2, 24, 18, 0, tzinfo=timezone.utc)
    forecasts = [
        ForecastPoint(
            source="a",
            station_id="KJFK",
            city="NYC",
            target_time_utc=target,
            expected_temp_f=73.0,
            low_f=71.0,
            high_f=75.0,
        ),
        ForecastPoint(
            source="b",
            station_id="KJFK",
            city="NYC",
            target_time_utc=target,
            expected_temp_f=73.5,
            low_f=72.0,
            high_f=75.0,
        ),
    ]
    market = WeatherMarket(
        market_id="m1",
        city="NYC",
        station_id="KJFK",
        target_time_utc=target,
        bucket_low_f=72.0,
        bucket_high_f=74.0,
    )
    quote = MarketQuote(
        market_id="m1",
        yes_bid=0.18,
        yes_ask=0.22,
        no_bid=0.78,
        no_ask=0.82,
        depth_yes_top=100.0,
        depth_no_top=100.0,
        last_price_yes=0.2,
        as_of_utc=target,
    )
    obs = ObservationSnapshot(
        station_id="KJFK",
        city="NYC",
        observed_at_utc=target,
        temp_f=72.9,
        source="mock",
    )

    opp = build_opportunity(market, quote, forecasts, obs, ScanConfig(), datetime.now(timezone.utc))
    assert 0.0 <= opp.model_prob_yes <= 1.0
    assert 0.0 <= opp.confidence_score <= 1.0
    assert 0.0 <= opp.liquidity_score <= 1.0
    assert 0.0 <= opp.uncertainty_score <= 1.0
