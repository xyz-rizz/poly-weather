from __future__ import annotations

from datetime import datetime
from math import erf, sqrt
from zoneinfo import ZoneInfo

from weather_bot.core.calibration_profile import apply_runtime_probability_calibration
from weather_bot.core.config import ScanConfig
from weather_bot.models.domain import ForecastPoint, MarketQuote, ObservationSnapshot, Opportunity, WeatherMarket


def _normal_cdf(x: float, mean: float, std: float) -> float:
    if std <= 0:
        return 1.0 if x >= mean else 0.0
    z = (x - mean) / (std * sqrt(2))
    return 0.5 * (1 + erf(z))


def estimate_bucket_probability(points: list[ForecastPoint], market: WeatherMarket) -> tuple[float, float, float]:
    if not points:
        return 0.5, 0.0, 1.0

    weighted_probs: list[tuple[float, float]] = []
    for p in points:
        if p.low_f is not None and p.high_f is not None and p.high_f > p.low_f:
            std = max((p.high_f - p.low_f) / 4.0, 1.5)
        else:
            std = 3.0
        if p.source.startswith("nws-daily"):
            std = max(std, 3.5)
        pop = p.pop_pct if p.pop_pct is not None else 0.0
        cloud = p.cloud_cover_pct if p.cloud_cover_pct is not None else 0.0
        wx_risk = p.weather_risk_score if p.weather_risk_score is not None else 0.0
        # Weather instability widens bucket uncertainty for daily highs.
        std *= (1.0 + min(0.50, (pop / 100.0) * 0.30 + (cloud / 100.0) * 0.10 + wx_risk * 0.35))
        upper = _normal_cdf(market.bucket_high_f, p.expected_temp_f, std) if market.bucket_high_f < 900 else 1.0
        lower = _normal_cdf(market.bucket_low_f, p.expected_temp_f, std) if market.bucket_low_f > -900 else 0.0
        prob = max(0.0, min(1.0, upper - lower))
        # Calibration floor/ceiling: daily weather uncertainty is rarely truly 0%/100%.
        prob = min(0.995, max(0.005, prob))
        weight = _source_weight(p.source)
        weighted_probs.append((prob, weight))

    probs = [p for p, _ in weighted_probs]
    total_weight = sum(w for _, w in weighted_probs) or 1.0
    mean_prob = sum(p * w for p, w in weighted_probs) / total_weight
    spread = (max(probs) - min(probs)) if len(probs) > 1 else 0.0
    consensus = 1.0 - spread
    return mean_prob, consensus, spread


def _source_weight(source: str) -> float:
    s = source.lower()
    if "hourly-path-high" in s:
        return 0.50
    if "daily-high" in s:
        return 0.35
    if "hourly" in s:
        return 0.20
    return 0.25


def implied_yes_mid(quote: MarketQuote) -> float:
    yes_mid = (quote.yes_bid + quote.yes_ask) / 2
    return max(0.0, min(1.0, yes_mid))


def liquidity_score(quote: MarketQuote, cfg: ScanConfig) -> float:
    spread = max(0.0, quote.yes_ask - quote.yes_bid)
    depth = min(quote.depth_yes_top, quote.depth_no_top)
    spread_component = max(0.0, 1.0 - (spread / max(cfg.max_spread, 1e-9)))
    depth_component = min(1.0, depth / max(cfg.min_top_depth, 1e-9))
    return 0.6 * spread_component + 0.4 * depth_component


def observation_alignment_score(obs: ObservationSnapshot, forecasts: list[ForecastPoint], now_utc: datetime) -> float:
    # Simple placeholder: score higher when observations sit near the ensemble mean trajectory.
    if not forecasts:
        return 0.5
    expected = sum(p.expected_temp_f for p in forecasts) / len(forecasts)
    deviation = abs(obs.temp_f - expected)
    return max(0.0, 1.0 - (deviation / 10.0))


def build_opportunity(
    market: WeatherMarket,
    quote: MarketQuote,
    forecasts: list[ForecastPoint],
    obs: ObservationSnapshot,
    cfg: ScanConfig,
    now_utc: datetime,
) -> Opportunity:
    model_prob, consensus_score, uncertainty_spread = estimate_bucket_probability(forecasts, market)
    model_prob = _apply_observation_bounds(model_prob, market, obs)
    model_prob = _apply_local_day_high_time_adjustment(model_prob, market, obs, now_utc, forecasts)
    cal = apply_runtime_probability_calibration(model_prob, market=market, now_utc=now_utc)
    model_prob = cal.calibrated_prob
    mid = implied_yes_mid(quote)
    edge = model_prob - mid
    liq = liquidity_score(quote, cfg)
    uncertainty_score = max(0.0, 1.0 - uncertainty_spread)
    obs_score = observation_alignment_score(obs, forecasts, now_utc)
    wx_instability = forecast_weather_instability_score(forecasts)
    uncertainty_score = max(0.0, uncertainty_score * (1.0 - 0.35 * wx_instability))
    obs_score = max(0.0, obs_score * (1.0 - 0.20 * wx_instability))
    w = cfg.score_weights
    confidence = (
        w["consensus"] * consensus_score
        + w["liquidity"] * liq
        + w["uncertainty"] * uncertainty_score
        + w["obs_alignment"] * obs_score
    )

    reasons: list[str] = []
    if edge >= cfg.min_edge:
        reasons.append("Undervalued YES bucket by model probability")
    elif edge <= -cfg.min_edge:
        reasons.append("Overvalued YES bucket by model probability")
    if uncertainty_spread < 0.20:
        reasons.append("Forecast sources broadly aligned")
    if liq > 0.6:
        reasons.append("Liquidity acceptable for small-size testing")
    if wx_instability >= 0.5:
        reasons.append("Elevated weather instability (precip/cloud risk) reduced confidence")
    if cal.profile_hit:
        reasons.append(f"Probability calibrated from empirical profile ({cal.profile_key})")

    return Opportunity(
        market=market,
        quote=quote,
        implied_yes_mid=mid,
        model_prob_yes=model_prob,
        edge=edge,
        confidence_score=confidence,
        liquidity_score=liq,
        uncertainty_score=uncertainty_score,
        reasons=reasons,
    )


def forecast_weather_instability_score(forecasts: list[ForecastPoint]) -> float:
    if not forecasts:
        return 0.0
    vals: list[float] = []
    for p in forecasts:
        pop = (p.pop_pct or 0.0) / 100.0
        cloud = (p.cloud_cover_pct or 0.0) / 100.0
        wx = p.weather_risk_score or 0.0
        vals.append(max(0.0, min(1.0, 0.45 * pop + 0.15 * cloud + 0.40 * wx)))
    if not vals:
        return 0.0
    return max(vals) * 0.6 + (sum(vals) / len(vals)) * 0.4


def _apply_observation_bounds(model_prob: float, market: WeatherMarket, obs: ObservationSnapshot) -> float:
    if market.settlement_metric != "highest_temperature":
        return model_prob
    # Current observed temperature is a lower bound on the daily high.
    # If the bucket is entirely below the current observed temp, YES is effectively impossible.
    if market.bucket_high_f < 900 and obs.temp_f > market.bucket_high_f:
        return 0.001
    # If this is a lower-tail bucket (e.g., "41F or below") and obs already exceeds threshold, impossible.
    if market.bucket_low_f <= -900 and market.bucket_high_f < 900 and obs.temp_f > market.bucket_high_f:
        return 0.001
    # If this is an upper-tail bucket ("X or higher") and current temp already at threshold, increase floor.
    if market.bucket_low_f > -900 and market.bucket_high_f >= 900 and obs.temp_f >= market.bucket_low_f:
        return max(model_prob, 0.55)
    return model_prob


def _apply_local_day_high_time_adjustment(
    model_prob: float,
    market: WeatherMarket,
    obs: ObservationSnapshot,
    now_utc: datetime,
    forecasts: list[ForecastPoint],
) -> float:
    if market.settlement_metric != "highest_temperature":
        return model_prob
    tz = _market_timezone(market)
    if tz is None:
        return model_prob

    now_local = now_utc.astimezone(tz)
    obs_local = obs.observed_at_utc.astimezone(tz)
    if market.market_date_local:
        try:
            market_date = datetime.fromisoformat(market.market_date_local).date()
        except ValueError:
            market_date = now_local.date()
    else:
        market_date = now_local.date()
    # If observation belongs to a different local date, do not force time-of-day adjustments.
    if obs_local.date() != market_date:
        return model_prob

    path_high_point = next((p for p in forecasts if "hourly-path-high" in p.source.lower()), None)
    projected_high = path_high_point.expected_temp_f if path_high_point else None
    remaining_to_peak = max(0.0, 17.0 - (now_local.hour + now_local.minute / 60.0))
    late_day_factor = max(0.0, min(1.0, (now_local.hour - 15) / 5.0))  # ramps from ~3pm to ~8pm local

    adjusted = model_prob
    if projected_high is not None:
        # If the projected path high sits materially below a bucket, penalize late-day YES probability.
        if market.bucket_low_f > -900 and projected_high + 1.0 < market.bucket_low_f:
            adjusted *= (1.0 - 0.6 * max(late_day_factor, 0.3))
        # If projected path high clearly exceeds the bucket top, reduce center/low bucket odds later in day.
        if market.bucket_high_f < 900 and projected_high - 1.0 > market.bucket_high_f and late_day_factor > 0:
            adjusted *= (1.0 - 0.4 * late_day_factor)

    # Late in the day, the daily high is less likely to move far above current observed temperature.
    if remaining_to_peak <= 2.0:
        if market.bucket_low_f > -900 and market.bucket_low_f >= obs.temp_f + 4:
            adjusted *= 0.5
        if market.bucket_high_f < 900 and market.bucket_high_f <= obs.temp_f + 1:
            adjusted = max(adjusted, min(0.95, adjusted + 0.05))

    return min(0.999, max(0.001, adjusted))


def _market_timezone(market: WeatherMarket) -> ZoneInfo | None:
    mapping = {
        "ET": "America/New_York",
        "EST": "America/New_York",
        "EDT": "America/New_York",
        "CT": "America/Chicago",
        "CST": "America/Chicago",
        "CDT": "America/Chicago",
        "PT": "America/Los_Angeles",
        "PST": "America/Los_Angeles",
        "PDT": "America/Los_Angeles",
        "UTC": "UTC",
    }
    label = market.timezone_name or "UTC"
    try:
        return ZoneInfo(mapping.get(label, label))
    except Exception:
        return None
