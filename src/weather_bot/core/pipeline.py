from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from weather_bot.core.config import ScanConfig
from weather_bot.core.interfaces import ForecastSource, MarketSource, ObservationSource
from weather_bot.core.scoring import build_opportunity
from weather_bot.models.domain import ForecastPoint, MarketQuote, ObservationSnapshot, Opportunity, WeatherMarket


@dataclass
class MarketEvaluation:
    market: WeatherMarket
    forecasts: list[ForecastPoint]
    observation: ObservationSnapshot | None
    quote: MarketQuote | None
    opportunity: Opportunity | None
    status: str
    reason: str | None = None


@dataclass
class ScanResult:
    scanned_at_utc: datetime
    opportunities: list[Opportunity]
    skipped_markets: list[str]
    evaluations: list[MarketEvaluation]


class WeatherScanPipeline:
    def __init__(
        self,
        market_source: MarketSource,
        forecast_sources: list[ForecastSource],
        observation_source: ObservationSource,
        config: ScanConfig,
    ) -> None:
        self.market_source = market_source
        self.forecast_sources = forecast_sources
        self.observation_source = observation_source
        self.config = config

    def run_scan(self) -> ScanResult:
        now_utc = datetime.now(timezone.utc)
        markets = [m for m in self.market_source.list_markets() if m.city in self.config.cities]
        ladder_issues = self._validate_market_ladders(markets)
        opportunities: list[Opportunity] = []
        skipped: list[str] = []
        evaluations: list[MarketEvaluation] = []
        pending_evaluations: list[MarketEvaluation] = []
        forecast_cache: dict[tuple[str, str, datetime], tuple[list[ForecastPoint], list[str]]] = {}
        obs_cache: dict[tuple[str, str], ObservationSnapshot] = {}
        emitted_forecast_errors: set[tuple[str, str]] = set()

        for market in markets:
            if now_utc > market.target_time_utc and (now_utc - market.target_time_utc).total_seconds() > self.config.market_expiry_grace_seconds:
                reason = "market expired"
                skipped.append(f"{market.market_id}: {reason}")
                evaluations.append(
                    MarketEvaluation(
                        market=market,
                        forecasts=[],
                        observation=None,
                        quote=None,
                        opportunity=None,
                        status="skipped",
                        reason=reason,
                    )
                )
                continue
            forecast_key = (market.city, market.station_id, market.target_time_utc)
            if forecast_key not in forecast_cache:
                forecast_cache[forecast_key] = self._collect_forecasts(market.city, market.station_id, market.target_time_utc)
            forecast_points, forecast_errors = forecast_cache[forecast_key]
            for err in forecast_errors:
                dedupe_key = (market.station_id, err)
                if dedupe_key not in emitted_forecast_errors:
                    skipped.append(f"{market.market_id}: forecast source error: {err}")
                    emitted_forecast_errors.add(dedupe_key)
            if not forecast_points:
                reason = "no forecasts"
                skipped.append(f"{market.market_id}: {reason}")
                evaluations.append(
                    MarketEvaluation(
                        market=market,
                        forecasts=[],
                        observation=None,
                        quote=None,
                        opportunity=None,
                        status="skipped",
                        reason=reason,
                    )
                )
                continue
            distinct_sources = {p.source for p in forecast_points}
            if len(distinct_sources) < self.config.min_forecast_sources:
                reason = f"insufficient forecast sources ({len(distinct_sources)}: {','.join(sorted(distinct_sources))})"
                skipped.append(f"{market.market_id}: {reason}")
                evaluations.append(
                    MarketEvaluation(
                        market=market,
                        forecasts=list(forecast_points),
                        observation=None,
                        quote=None,
                        opportunity=None,
                        status="skipped",
                        reason=reason,
                    )
                )
                continue
            try:
                obs_key = (market.city, market.station_id)
                if obs_key not in obs_cache:
                    obs_cache[obs_key] = self.observation_source.fetch_latest(market.city, market.station_id)
                obs = obs_cache[obs_key]
            except Exception as exc:
                reason = f"observation source error: {exc}"
                skipped.append(f"{market.market_id}: {reason}")
                evaluations.append(
                    MarketEvaluation(
                        market=market,
                        forecasts=list(forecast_points),
                        observation=None,
                        quote=None,
                        opportunity=None,
                        status="skipped",
                        reason=reason,
                    )
                )
                continue
            obs_age_min = (now_utc - obs.observed_at_utc).total_seconds() / 60.0
            if obs_age_min > self.config.max_observation_age_minutes:
                reason = f"observation stale ({obs_age_min:.1f}m)"
                skipped.append(f"{market.market_id}: {reason}")
                evaluations.append(
                    MarketEvaluation(
                        market=market,
                        forecasts=list(forecast_points),
                        observation=obs,
                        quote=None,
                        opportunity=None,
                        status="skipped",
                        reason=reason,
                    )
                )
                continue
            try:
                quote = self.market_source.fetch_quote(market.market_id)
            except Exception as exc:
                reason = f"market source error: {exc}"
                skipped.append(f"{market.market_id}: {reason}")
                evaluations.append(
                    MarketEvaluation(
                        market=market,
                        forecasts=list(forecast_points),
                        observation=obs,
                        quote=None,
                        opportunity=None,
                        status="skipped",
                        reason=reason,
                    )
                )
                continue
            quote_age_sec = (now_utc - quote.as_of_utc).total_seconds()
            quote_age_limit = self._quote_age_limit_seconds(market, quote, now_utc)
            if quote_age_sec > quote_age_limit:
                reason = f"quote stale ({quote_age_sec:.1f}s > {quote_age_limit:.0f}s)"
                skipped.append(f"{market.market_id}: {reason}")
                evaluations.append(
                    MarketEvaluation(
                        market=market,
                        forecasts=list(forecast_points),
                        observation=obs,
                        quote=quote,
                        opportunity=None,
                        status="skipped",
                        reason=reason,
                    )
                )
                continue
            spread = quote.yes_ask - quote.yes_bid
            if spread > self.config.max_spread:
                reason = f"spread {spread:.3f} too wide"
                skipped.append(f"{market.market_id}: {reason}")
                evaluations.append(
                    MarketEvaluation(
                        market=market,
                        forecasts=list(forecast_points),
                        observation=obs,
                        quote=quote,
                        opportunity=None,
                        status="skipped",
                        reason=reason,
                    )
                )
                continue
            opp = build_opportunity(
                market=market,
                quote=quote,
                forecasts=forecast_points,
                obs=obs,
                cfg=self.config,
                now_utc=now_utc,
            )
            pending_evaluations.append(
                MarketEvaluation(
                    market=market,
                    forecasts=list(forecast_points),
                    observation=obs,
                    quote=quote,
                    opportunity=opp,
                    status="pending",
                    reason=None,
                )
            )

        self._normalize_event_ladders(pending_evaluations)
        for ev in pending_evaluations:
            opp = ev.opportunity
            if opp is None:
                continue
            group_id = self._event_group_id(ev.market)
            if group_id in ladder_issues:
                ev.status = "skipped"
                ev.reason = f"event ladder invalid: {ladder_issues[group_id]}"
                skipped.append(f"{ev.market.market_id}: {ev.reason}")
            elif abs(opp.edge) < self.config.min_edge:
                ev.status = "skipped"
                ev.reason = f"edge {opp.edge:.3f} below threshold"
                skipped.append(f"{ev.market.market_id}: {ev.reason}")
            elif opp.confidence_score < self.config.min_confidence_score:
                ev.status = "skipped"
                ev.reason = f"confidence {opp.confidence_score:.3f} below threshold"
                skipped.append(f"{ev.market.market_id}: {ev.reason}")
            else:
                ev.status = "opportunity"
                ev.reason = None
                opportunities.append(opp)
            evaluations.append(ev)

        self._select_opportunities_per_event(opportunities, evaluations, skipped)
        opportunities.sort(key=lambda o: (abs(o.edge) * o.confidence_score), reverse=True)
        return ScanResult(
            scanned_at_utc=now_utc,
            opportunities=opportunities,
            skipped_markets=skipped,
            evaluations=evaluations,
        )

    def _collect_forecasts(self, city: str, station_id: str, target_time_utc: datetime) -> tuple[list[ForecastPoint], list[str]]:
        points: list[ForecastPoint] = []
        errors: list[str] = []
        for source in self.forecast_sources:
            try:
                snapshot = source.fetch_forecasts([city], target_time_utc)
            except Exception as exc:
                errors.append(f"{getattr(source, 'name', source.__class__.__name__)}: {exc}")
                continue
            for p in snapshot.points:
                if p.city != city or p.station_id != station_id or p.target_time_utc != target_time_utc:
                    continue
                if p.updated_at_utc is not None:
                    age_min = (datetime.now(timezone.utc) - p.updated_at_utc).total_seconds() / 60.0
                    max_age = self._forecast_max_age_minutes(p.source)
                    if age_min > max_age:
                        errors.append(
                            f"{getattr(source, 'name', source.__class__.__name__)}: stale forecast ({age_min:.1f}m > {max_age:.0f}m)"
                        )
                        continue
                points.append(p)
        return points, errors

    def _forecast_max_age_minutes(self, source_name: str) -> float:
        s = source_name.lower()
        if "daily" in s:
            return self.config.max_forecast_age_minutes_daily
        if "hourly" in s:
            return self.config.max_forecast_age_minutes_hourly
        return self.config.max_forecast_age_minutes

    def _normalize_event_ladders(self, evaluations: list[MarketEvaluation]) -> None:
        groups: dict[tuple[str, str], list[MarketEvaluation]] = {}
        for ev in evaluations:
            if ev.opportunity is None:
                continue
            market = ev.market
            if market.settlement_metric != "highest_temperature":
                continue
            key = self._event_group_id(market)
            groups.setdefault(key, []).append(ev)

        for group in groups.values():
            if len(group) < 3:
                continue
            probs = [ev.opportunity.model_prob_yes for ev in group if ev.opportunity is not None]
            total = sum(probs)
            if total <= 0:
                continue
            # Normalize independent bucket probabilities into an event-level bucket ladder.
            for ev in group:
                opp = ev.opportunity
                if opp is None:
                    continue
                normalized = max(0.001, min(0.999, opp.model_prob_yes / total))
                if abs(normalized - opp.model_prob_yes) > 1e-6:
                    opp.model_prob_yes = normalized
                    opp.edge = opp.model_prob_yes - opp.implied_yes_mid
                    self._refresh_edge_reason(opp)
                    if "Event bucket ladder normalized" not in opp.reasons:
                        opp.reasons.append("Event bucket ladder normalized")

    def _validate_market_ladders(self, markets: list[WeatherMarket]) -> dict[tuple[str, str], str]:
        groups: dict[tuple[str, str], list[WeatherMarket]] = {}
        for m in markets:
            if m.settlement_metric != "highest_temperature":
                continue
            if not m.event_slug:
                continue
            groups.setdefault(self._event_group_id(m), []).append(m)

        issues: dict[tuple[str, str], str] = {}
        for key, markets in groups.items():
            if len(markets) < 3:
                issues[key] = "too few buckets"
                continue
            seen_ids = set()
            lower_tails = 0
            upper_tails = 0
            ranges: list[tuple[float, float]] = []
            duplicate = False
            overlap = False
            for m in markets:
                if m.market_id in seen_ids:
                    duplicate = True
                seen_ids.add(m.market_id)
                if m.bucket_low_f <= -900:
                    lower_tails += 1
                elif m.bucket_high_f >= 900:
                    upper_tails += 1
                else:
                    ranges.append((m.bucket_low_f, m.bucket_high_f))
            if duplicate:
                issues[key] = "duplicate market ids in ladder"
                continue
            if lower_tails != 1 or upper_tails != 1:
                issues[key] = f"tail bucket mismatch (lower={lower_tails}, upper={upper_tails})"
                continue
            ranges.sort()
            for i in range(1, len(ranges)):
                prev = ranges[i - 1]
                cur = ranges[i]
                if cur[0] <= prev[1]:
                    overlap = True
                    break
            if overlap:
                issues[key] = "overlapping range buckets"
                continue
        return issues

    @staticmethod
    def _event_group_id(market: WeatherMarket) -> tuple[str, str]:
        group_id = market.event_slug or f"{market.city}|{market.station_id}|{market.target_time_utc.isoformat()}"
        return (market.city, group_id)

    def _refresh_edge_reason(self, opp: Opportunity) -> None:
        opp.reasons = [
            r
            for r in opp.reasons
            if r not in {"Undervalued YES bucket by model probability", "Overvalued YES bucket by model probability"}
        ]
        if opp.edge >= self.config.min_edge:
            opp.reasons.insert(0, "Undervalued YES bucket by model probability")
        elif opp.edge <= -self.config.min_edge:
            opp.reasons.insert(0, "Overvalued YES bucket by model probability")

    def _quote_age_limit_seconds(self, market: WeatherMarket, quote: MarketQuote, now_utc: datetime) -> float:
        base = self.config.max_quote_age_seconds
        if market.settlement_metric != "highest_temperature":
            return base
        hours_to_end = max(0.0, (market.target_time_utc - now_utc).total_seconds() / 3600.0)
        if hours_to_end >= 12:
            limit = self.config.max_quote_age_seconds_far
        elif hours_to_end >= 4:
            limit = self.config.max_quote_age_seconds_mid
        else:
            limit = self.config.max_quote_age_seconds_near
        spread = max(0.0, quote.yes_ask - quote.yes_bid)
        if spread <= 0.05 and min(quote.depth_yes_top, quote.depth_no_top) >= self.config.min_top_depth:
            limit += 300.0
        return limit

    def _select_opportunities_per_event(
        self,
        opportunities: list[Opportunity],
        evaluations: list[MarketEvaluation],
        skipped: list[str],
    ) -> None:
        if self.config.max_opportunities_per_event <= 0:
            return
        groups: dict[tuple[str, str], list[Opportunity]] = {}
        for opp in opportunities:
            groups.setdefault(self._event_group_id(opp.market), []).append(opp)
        allowed_ids: set[str] = set()
        for _, group in groups.items():
            ranked = sorted(group, key=self._event_selection_score, reverse=True)
            for opp in ranked[: self.config.max_opportunities_per_event]:
                allowed_ids.add(opp.market.market_id)
                if "Selected by event optimizer" not in opp.reasons:
                    opp.reasons.append("Selected by event optimizer")
        if len(allowed_ids) == len(opportunities):
            return
        kept = [opp for opp in opportunities if opp.market.market_id in allowed_ids]
        removed_ids = {opp.market.market_id for opp in opportunities if opp.market.market_id not in allowed_ids}
        opportunities[:] = kept
        for ev in evaluations:
            if ev.status != "opportunity" or ev.opportunity is None:
                continue
            if ev.market.market_id in removed_ids:
                ev.status = "skipped"
                ev.reason = "event optimizer rejected"
                skipped.append(f"{ev.market.market_id}: event opportunity cap")

    @staticmethod
    def _event_selection_score(opp: Opportunity) -> float:
        spread = max(0.0, opp.quote.yes_ask - opp.quote.yes_bid)
        # Prefer actionable prices over dust tails while still allowing good tail dislocations.
        buy_price = opp.implied_yes_mid if opp.edge > 0 else (1.0 - opp.implied_yes_mid)
        dust_penalty = 0.25 if buy_price < 0.03 else 0.0
        ultra_tail_penalty = 0.15 if buy_price < 0.01 else 0.0
        spread_penalty = min(0.5, spread * 2.0)
        confidence_edge = abs(opp.edge) * opp.confidence_score
        liquidity_bonus = 0.15 * opp.liquidity_score
        uncertainty_bonus = 0.05 * opp.uncertainty_score
        return confidence_edge + liquidity_bonus + uncertainty_bonus - spread_penalty - dust_penalty - ultra_tail_penalty
