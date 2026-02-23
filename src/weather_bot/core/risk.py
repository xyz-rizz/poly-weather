from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone

from weather_bot.core.config import ScanConfig
from weather_bot.models.domain import Opportunity
from weather_bot.models.risk import PlannedPaperOrder, PortfolioState, RiskDecision


class RiskEngine:
    def __init__(self, config: ScanConfig) -> None:
        self.config = config

    def evaluate(self, opportunity: Opportunity, state: PortfolioState, proposed_size_usd: float) -> RiskDecision:
        if proposed_size_usd <= 0:
            return RiskDecision(False, "non-positive size")
        if proposed_size_usd > self.config.max_position_size_usd:
            return RiskDecision(False, "exceeds max position size")
        if len(state.open_positions) >= self.config.max_open_positions:
            return RiskDecision(False, "max open positions reached")
        if state.realized_pnl_today_usd <= -abs(self.config.daily_loss_cap_usd):
            return RiskDecision(False, "daily loss cap reached")

        city = opportunity.market.city
        event_slug = opportunity.market.event_slug or f"{opportunity.market.city}|{opportunity.market.target_time_utc.date().isoformat()}"
        city_count = sum(1 for p in state.open_positions if p.city == city)
        if city_count >= self.config.max_positions_per_city:
            return RiskDecision(False, "max positions for city reached")
        event_count = sum(
            1
            for p in state.open_positions
            if (p.event_slug or f"{p.city}|unknown") == event_slug
        )
        if event_count >= self.config.max_positions_per_event:
            return RiskDecision(False, "max positions for event reached")

        city_exposure = sum(p.size_usd for p in state.open_positions if p.city == city)
        if city_exposure + proposed_size_usd > self.config.max_city_exposure_usd:
            return RiskDecision(False, "max city exposure exceeded")

        existing_market = any(p.market_id == opportunity.market.market_id for p in state.open_positions)
        if existing_market:
            return RiskDecision(False, "position already open in market")

        if opportunity.liquidity_score < 0.4:
            return RiskDecision(False, "liquidity score too low")
        if opportunity.confidence_score < self.config.min_confidence_score:
            return RiskDecision(False, "confidence below threshold")

        return RiskDecision(True, "accepted")

    def plan_orders(self, opportunities: list[Opportunity], state: PortfolioState) -> list[PlannedPaperOrder]:
        plans: list[PlannedPaperOrder] = []
        working_state = PortfolioState(
            as_of_utc=state.as_of_utc,
            realized_pnl_today_usd=state.realized_pnl_today_usd,
            open_positions=list(state.open_positions),
        )
        for opp in opportunities:
            size = min(self.config.paper_trade_size_usd, self.config.max_position_size_usd)
            direction = "BUY_YES" if opp.edge > 0 else "BUY_NO"
            ref_price = opp.implied_yes_mid if direction == "BUY_YES" else 1.0 - opp.implied_yes_mid
            if ref_price < self.config.min_position_entry_price:
                plans.append(
                    PlannedPaperOrder(
                        market_id=opp.market.market_id,
                        event_slug=opp.market.event_slug,
                        city=opp.market.city,
                        direction=direction,
                        size_usd=size,
                        ref_price=ref_price,
                        confidence_score=opp.confidence_score,
                        edge=opp.edge,
                        accepted=False,
                        reason="entry price below minimum",
                    )
                )
                continue
            decision = self.evaluate(opp, working_state, size)
            plans.append(
                PlannedPaperOrder(
                    market_id=opp.market.market_id,
                    event_slug=opp.market.event_slug,
                    city=opp.market.city,
                    direction=direction,
                    size_usd=size,
                    ref_price=ref_price,
                    confidence_score=opp.confidence_score,
                    edge=opp.edge,
                    accepted=decision.accepted,
                    reason=decision.reason,
                )
            )
            if decision.accepted:
                from weather_bot.models.risk import OpenPaperPosition

                working_state.open_positions.append(
                    OpenPaperPosition(
                        market_id=opp.market.market_id,
                        event_slug=opp.market.event_slug,
                        city=opp.market.city,
                        direction=direction,
                        size_usd=size,
                        opened_at_utc=datetime.now(timezone.utc),
                        entry_ref_price=opp.implied_yes_mid,
                    )
                )
        return plans


def portfolio_summary(state: PortfolioState) -> dict[str, float | int]:
    city_exposure = defaultdict(float)
    counts = Counter()
    for pos in state.open_positions:
        city_exposure[pos.city] += pos.size_usd
        counts[pos.city] += 1
    summary: dict[str, float | int] = {
        "open_positions": len(state.open_positions),
        "realized_pnl_today_usd": state.realized_pnl_today_usd,
    }
    for city, exposure in city_exposure.items():
        summary[f"exposure_{city}"] = round(exposure, 2)
        summary[f"count_{city}"] = counts[city]
    return summary
