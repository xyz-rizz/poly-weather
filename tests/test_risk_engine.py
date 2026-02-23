from datetime import datetime, timezone

from weather_bot.core.config import ScanConfig
from weather_bot.core.risk import RiskEngine
from weather_bot.models.domain import MarketQuote, Opportunity, WeatherMarket
from weather_bot.models.risk import OpenPaperPosition, PortfolioState


def _opp(
    market_id: str,
    city: str,
    edge: float = 0.2,
    conf: float = 0.8,
    liq: float = 0.8,
    event_slug: str = "",
) -> Opportunity:
    market = WeatherMarket(
        market_id=market_id,
        city=city,
        station_id="TEST",
        target_time_utc=datetime.now(timezone.utc),
        bucket_low_f=70,
        bucket_high_f=72,
        event_slug=event_slug,
    )
    quote = MarketQuote(
        market_id=market_id,
        yes_bid=0.2,
        yes_ask=0.22,
        no_bid=0.78,
        no_ask=0.8,
        depth_yes_top=100,
        depth_no_top=100,
        last_price_yes=0.21,
        as_of_utc=datetime.now(timezone.utc),
    )
    return Opportunity(
        market=market,
        quote=quote,
        implied_yes_mid=0.21,
        model_prob_yes=0.41 if edge > 0 else 0.1,
        edge=edge,
        confidence_score=conf,
        liquidity_score=liq,
        uncertainty_score=0.9,
        reasons=[],
    )


def test_risk_engine_blocks_duplicate_market() -> None:
    cfg = ScanConfig()
    engine = RiskEngine(cfg)
    state = PortfolioState(
        open_positions=[
            OpenPaperPosition(
                market_id="m1",
                event_slug="e1",
                city="NYC",
                direction="BUY_YES",
                size_usd=5.0,
                opened_at_utc=datetime.now(timezone.utc),
                entry_ref_price=0.2,
            )
        ]
    )
    decision = engine.evaluate(_opp("m1", "NYC"), state, proposed_size_usd=5.0)
    assert not decision.accepted
    assert "already open" in decision.reason


def test_risk_engine_blocks_city_exposure_limit() -> None:
    cfg = ScanConfig(max_city_exposure_usd=8.0, paper_trade_size_usd=5.0)
    engine = RiskEngine(cfg)
    state = PortfolioState(
        open_positions=[
            OpenPaperPosition(
                market_id="m1",
                event_slug="e1",
                city="NYC",
                direction="BUY_YES",
                size_usd=5.0,
                opened_at_utc=datetime.now(timezone.utc),
                entry_ref_price=0.2,
            )
        ]
    )
    decision = engine.evaluate(_opp("m2", "NYC"), state, proposed_size_usd=5.0)
    assert not decision.accepted
    assert "city exposure" in decision.reason


def test_risk_engine_blocks_event_position_limit() -> None:
    cfg = ScanConfig(max_positions_per_event=1)
    engine = RiskEngine(cfg)
    state = PortfolioState(
        open_positions=[
            OpenPaperPosition(
                market_id="m1",
                event_slug="event-abc",
                city="NYC",
                direction="BUY_YES",
                size_usd=5.0,
                opened_at_utc=datetime.now(timezone.utc),
                entry_ref_price=0.2,
            )
        ]
    )
    opp = _opp("m2", "NYC", event_slug="event-abc")
    decision = engine.evaluate(opp, state, proposed_size_usd=5.0)
    assert not decision.accepted
    assert "event" in decision.reason
