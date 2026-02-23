from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class OpenPaperPosition:
    market_id: str
    event_slug: str
    city: str
    direction: str
    size_usd: float
    opened_at_utc: datetime
    entry_ref_price: float


@dataclass
class PortfolioState:
    as_of_utc: datetime | None = None
    realized_pnl_today_usd: float = 0.0
    open_positions: list[OpenPaperPosition] = field(default_factory=list)


@dataclass(frozen=True)
class RiskDecision:
    accepted: bool
    reason: str


@dataclass(frozen=True)
class PlannedPaperOrder:
    market_id: str
    event_slug: str
    city: str
    direction: str
    size_usd: float
    ref_price: float
    confidence_score: float
    edge: float
    accepted: bool
    reason: str


@dataclass(frozen=True)
class PlannedPaperExit:
    market_id: str
    city: str
    direction: str
    size_usd: float
    entry_ref_price: float
    mark_yes_mid: float
    mark_position_price: float
    unrealized_pnl_usd: float
    unrealized_return_pct: float
    trigger: str
    accepted: bool
    reason: str
