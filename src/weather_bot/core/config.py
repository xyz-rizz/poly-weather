from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ScanConfig:
    strategy_id: str = "weather-baseline-v1"
    cities: list[str] = field(default_factory=lambda: ["NYC", "Atlanta", "Dallas", "Chicago", "Seattle"])
    min_edge: float = 0.08
    min_confidence_score: float = 0.55
    max_spread: float = 0.18
    min_top_depth: float = 20.0
    min_forecast_sources: int = 2
    paper_trade_size_usd: float = 5.0
    max_position_size_usd: float = 10.0
    max_open_positions: int = 10
    max_positions_per_city: int = 3
    max_positions_per_event: int = 2
    max_city_exposure_usd: float = 30.0
    daily_loss_cap_usd: float = 25.0
    min_position_entry_price: float = 0.03
    take_profit_pct: float = 0.35
    stop_loss_pct: float = 0.20
    max_forecast_age_minutes: float = 180.0
    max_forecast_age_minutes_daily: float = 720.0
    max_forecast_age_minutes_hourly: float = 240.0
    max_observation_age_minutes: float = 90.0
    max_quote_age_seconds: float = 600.0
    max_quote_age_seconds_far: float = 1800.0
    max_quote_age_seconds_mid: float = 1200.0
    max_quote_age_seconds_near: float = 600.0
    market_expiry_grace_seconds: float = 300.0
    max_opportunities_per_event: int = 2
    score_weights: dict[str, float] = field(
        default_factory=lambda: {
            "consensus": 0.45,
            "liquidity": 0.20,
            "uncertainty": 0.20,
            "obs_alignment": 0.15,
        }
    )
