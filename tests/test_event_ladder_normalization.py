from datetime import datetime, timezone

from weather_bot.core.config import ScanConfig
from weather_bot.core.pipeline import MarketEvaluation, WeatherScanPipeline
from weather_bot.models.domain import ForecastPoint, MarketQuote, ObservationSnapshot, Opportunity, WeatherMarket


class _DummySource:
    def list_markets(self):  # pragma: no cover
        return []

    def fetch_quote(self, market_id):  # pragma: no cover
        raise NotImplementedError

    def fetch_latest(self, city, station_id):  # pragma: no cover
        raise NotImplementedError

    def fetch_forecasts(self, cities, target_time_utc):  # pragma: no cover
        raise NotImplementedError


def _opp(mid: float, prob: float, event_slug: str, market_id: str) -> Opportunity:
    target = datetime.now(timezone.utc)
    market = WeatherMarket(
        market_id=market_id,
        city="Atlanta",
        station_id="KATL",
        target_time_utc=target,
        bucket_low_f=40,
        bucket_high_f=41,
        event_slug=event_slug,
        settlement_metric="highest_temperature",
    )
    quote = MarketQuote(
        market_id=market_id,
        yes_bid=mid - 0.01,
        yes_ask=mid + 0.01,
        no_bid=1 - (mid + 0.01),
        no_ask=1 - (mid - 0.01),
        depth_yes_top=100,
        depth_no_top=100,
        last_price_yes=mid,
        as_of_utc=target,
    )
    return Opportunity(
        market=market,
        quote=quote,
        implied_yes_mid=mid,
        model_prob_yes=prob,
        edge=prob - mid,
        confidence_score=0.8,
        liquidity_score=0.8,
        uncertainty_score=0.8,
        reasons=[],
    )


def test_event_bucket_ladder_normalization_scales_probs_to_sum_one() -> None:
    pipeline = WeatherScanPipeline(_DummySource(), [], _DummySource(), ScanConfig())
    obs = ObservationSnapshot(
        station_id="KATL",
        city="Atlanta",
        observed_at_utc=datetime.now(timezone.utc),
        temp_f=45.0,
    )
    fp = ForecastPoint(
        source="mock",
        station_id="KATL",
        city="Atlanta",
        target_time_utc=datetime.now(timezone.utc),
        expected_temp_f=45.0,
    )
    evals = [
        MarketEvaluation(_opp(0.2, 0.6, "e1", "m1").market, [fp], obs, _opp(0.2, 0.6, "e1", "m1").quote, _opp(0.2, 0.6, "e1", "m1"), "pending"),
        MarketEvaluation(_opp(0.3, 0.7, "e1", "m2").market, [fp], obs, _opp(0.3, 0.7, "e1", "m2").quote, _opp(0.3, 0.7, "e1", "m2"), "pending"),
        MarketEvaluation(_opp(0.1, 0.2, "e1", "m3").market, [fp], obs, _opp(0.1, 0.2, "e1", "m3").quote, _opp(0.1, 0.2, "e1", "m3"), "pending"),
    ]
    pipeline._normalize_event_ladders(evals)
    total = sum(ev.opportunity.model_prob_yes for ev in evals if ev.opportunity)
    assert round(total, 6) == 1.0
