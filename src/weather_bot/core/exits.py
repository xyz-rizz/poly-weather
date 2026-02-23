from __future__ import annotations

from weather_bot.core.config import ScanConfig
from weather_bot.models.risk import PlannedPaperExit, PortfolioState


def plan_shadow_exits(state: PortfolioState, latest_yes_mid: dict[str, float], cfg: ScanConfig) -> list[PlannedPaperExit]:
    exits: list[PlannedPaperExit] = []
    for pos in state.open_positions:
        mark_yes = latest_yes_mid.get(pos.market_id)
        if mark_yes is None:
            exits.append(
                PlannedPaperExit(
                    market_id=pos.market_id,
                    city=pos.city,
                    direction=pos.direction,
                    size_usd=pos.size_usd,
                    entry_ref_price=pos.entry_ref_price,
                    mark_yes_mid=-1.0,
                    mark_position_price=-1.0,
                    unrealized_pnl_usd=0.0,
                    unrealized_return_pct=0.0,
                    trigger="none",
                    accepted=False,
                    reason="missing_mark",
                )
            )
            continue

        entry_pos_price = pos.entry_ref_price if pos.direction == "BUY_YES" else (1.0 - pos.entry_ref_price)
        mark_pos_price = mark_yes if pos.direction == "BUY_YES" else (1.0 - mark_yes)
        shares = pos.size_usd / max(entry_pos_price, 1e-9)
        unrealized = shares * (mark_pos_price - entry_pos_price)
        ret = (mark_pos_price - entry_pos_price) / max(entry_pos_price, 1e-9)

        trigger = "none"
        accepted = False
        reason = "hold"
        if ret >= cfg.take_profit_pct:
            trigger, accepted, reason = "take_profit", True, "tp hit"
        elif ret <= -abs(cfg.stop_loss_pct):
            trigger, accepted, reason = "stop_loss", True, "sl hit"

        exits.append(
            PlannedPaperExit(
                market_id=pos.market_id,
                city=pos.city,
                direction=pos.direction,
                size_usd=pos.size_usd,
                entry_ref_price=pos.entry_ref_price,
                mark_yes_mid=mark_yes,
                mark_position_price=mark_pos_price,
                unrealized_pnl_usd=unrealized,
                unrealized_return_pct=ret,
                trigger=trigger,
                accepted=accepted,
                reason=reason,
            )
        )
    return exits


def latest_yes_mid_from_evaluations(evaluations: list) -> dict[str, float]:
    mids: dict[str, float] = {}
    for ev in evaluations:
        market_id = ev.market.market_id
        if ev.opportunity is not None:
            mids[market_id] = ev.opportunity.implied_yes_mid
        elif ev.quote is not None:
            mids[market_id] = (ev.quote.yes_bid + ev.quote.yes_ask) / 2
    return mids
