from datetime import datetime, timezone

from weather_bot.core.config import ScanConfig
from weather_bot.core.exits import plan_shadow_exits
from weather_bot.models.risk import OpenPaperPosition, PortfolioState


def test_exit_planner_take_profit_trigger() -> None:
    state = PortfolioState(
        open_positions=[
            OpenPaperPosition(
                market_id="m1",
                event_slug="e1",
                city="NYC",
                direction="BUY_YES",
                size_usd=10.0,
                opened_at_utc=datetime.now(timezone.utc),
                entry_ref_price=0.20,
            )
        ]
    )
    cfg = ScanConfig(take_profit_pct=0.25, stop_loss_pct=0.2)
    exits = plan_shadow_exits(state, {"m1": 0.30}, cfg)
    assert len(exits) == 1
    assert exits[0].accepted is True
    assert exits[0].trigger == "take_profit"
