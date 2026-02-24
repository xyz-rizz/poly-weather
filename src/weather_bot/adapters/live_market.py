from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any
from urllib.parse import urlencode

from weather_bot.adapters.weather_rule_parser import parse_weather_market_rule
from weather_bot.core.universe import WeatherCityUniverseEntry
from weather_bot.core.interfaces import MarketSource
from weather_bot.models.domain import MarketQuote, WeatherMarket
from weather_bot.utils.http import JsonHttpClient


def _parse_dt(value: str | None) -> datetime:
    if not value:
        return datetime.now(UTC)
    text = value
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text).astimezone(UTC)
    except ValueError:
        return datetime.now(UTC)


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.lower() in {"1", "true", "yes"}
    return default


@dataclass(frozen=True)
class PolymarketGammaConfig:
    base_url: str = "https://gamma-api.polymarket.com/markets"
    events_url: str = "https://gamma-api.polymarket.com/events"
    limit: int = 200
    closed: bool = False
    archived: bool = False
    tag_hint: str = "weather"
    allowed_universe: tuple[WeatherCityUniverseEntry, ...] = ()
    require_wunderground_rules: bool = True
    use_event_slug_discovery: bool = False
    event_slugs: tuple[str, ...] = ()


class PolymarketGammaWeatherMarketSource(MarketSource):
    name = "polymarket-gamma-weather"

    def __init__(self, *, http_client: JsonHttpClient | None = None, config: PolymarketGammaConfig | None = None) -> None:
        self._http = http_client or JsonHttpClient()
        self._cfg = config or PolymarketGammaConfig()
        self._quote_cache: dict[str, MarketQuote] = {}
        self._market_cache: dict[str, WeatherMarket] = {}

    def list_markets(self) -> list[WeatherMarket]:
        rows: list[dict[str, Any]]
        if self._cfg.use_event_slug_discovery:
            rows = self._fetch_weather_markets_from_events()
        else:
            url = self._build_markets_url()
            payload = self._http.get_json(url)
            rows = payload if isinstance(payload, list) else payload.get("data", [])
        markets: list[WeatherMarket] = []
        self._quote_cache.clear()
        self._market_cache.clear()
        for row in rows:
            if not isinstance(row, dict):
                continue
            if not self._looks_weather(row):
                continue
            market = self._parse_market(row)
            if market is None:
                continue
            quote = self._parse_quote(row, market.market_id)
            self._market_cache[market.market_id] = market
            self._quote_cache[market.market_id] = quote
            markets.append(market)
        return markets

    def fetch_quote(self, market_id: str) -> MarketQuote:
        quote = self._quote_cache.get(market_id)
        if quote is None:
            raise ValueError(f"Quote not cached for market_id={market_id}; call list_markets() first")
        return quote

    def _build_markets_url(self) -> str:
        params = {
            "limit": self._cfg.limit,
            "closed": str(self._cfg.closed).lower(),
            "archived": str(self._cfg.archived).lower(),
        }
        return f"{self._cfg.base_url}?{urlencode(params)}"

    def _fetch_weather_markets_from_events(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        slugs = self._cfg.event_slugs
        for slug in slugs:
            url = f"{self._cfg.events_url}?{urlencode({'slug': slug})}"
            try:
                payload = self._http.get_json(url)
            except Exception:
                continue
            events = payload if isinstance(payload, list) else payload.get("data", [])
            if not events:
                continue
            event = events[0]
            if not isinstance(event, dict):
                continue
            event_markets = event.get("markets") or []
            if not isinstance(event_markets, list):
                continue
            for market_row in event_markets:
                if not isinstance(market_row, dict):
                    continue
                merged = dict(market_row)
                # event-level fields improve rule parsing and notes consistency
                for key in ("title", "description", "resolutionSource", "resolutionCriteria", "tags", "category", "subcategory"):
                    if key not in merged or merged.get(key) in (None, "", []):
                        merged[key] = event.get(key)
                if "eventSlug" not in merged:
                    merged["eventSlug"] = event.get("slug")
                rows.append(merged)
        return rows

    def _looks_weather(self, row: dict[str, Any]) -> bool:
        text = " ".join(
            str(v)
            for v in (
                row.get("question"),
                row.get("title"),
                row.get("description"),
                row.get("category"),
                row.get("subcategory"),
            )
            if v is not None
        ).lower()
        if "weather" in text or "temperature" in text or "forecast" in text:
            return True
        tags = row.get("tags") or []
        if isinstance(tags, list):
            for tag in tags:
                if isinstance(tag, str) and self._cfg.tag_hint in tag.lower():
                    return True
                if isinstance(tag, dict):
                    name = str(tag.get("name", "")).lower()
                    if self._cfg.tag_hint in name:
                        return True
        return False

    def _parse_market(self, row: dict[str, Any]) -> WeatherMarket | None:
        parsed = parse_weather_market_rule(row)
        if parsed is None or parsed.city is None or parsed.bucket_low is None or parsed.bucket_high is None:
            return None
        city = parsed.city

        if self._cfg.require_wunderground_rules and (parsed.settlement_source or "").lower() != "wunderground":
            return None
        if self._cfg.allowed_universe and not self._in_allowed_universe(parsed.city, parsed.station_id, parsed.unit):
            return None

        market_id = str(row.get("id") or row.get("conditionId") or row.get("slug") or "")
        if not market_id:
            return None
        yes_token_id, no_token_id = self._extract_outcome_token_ids(row)
        target_time = _parse_dt(
            row.get("endDate")
            or row.get("end_date_iso")
            or row.get("resolutionDate")
            or row.get("closeTime")
        )
        return WeatherMarket(
            market_id=market_id,
            city=city,
            station_id=(parsed.station_id or self._extract_station_id(row, city) or "UNKNOWN"),
            target_time_utc=target_time,
            bucket_low_f=float(parsed.bucket_low),
            bucket_high_f=float(parsed.bucket_high),
            event_slug=str(row.get("eventSlug") or row.get("slug") or ""),
            market_date_local=parsed.market_date.isoformat() if parsed.market_date else "",
            settlement_source=parsed.settlement_source or "unknown",
            settlement_metric=parsed.settlement_metric,
            boundary_semantics=parsed.boundary_semantics,
            timezone_name=parsed.timezone_name or "UTC",
            resolution_notes=parsed.notes,
            yes_token_id=yes_token_id,
            no_token_id=no_token_id,
            clob_market_id=str(row.get("clobMarketId") or row.get("conditionId") or market_id),
        )

    def _parse_quote(self, row: dict[str, Any], market_id: str) -> MarketQuote:
        yes_bid = _as_float(row.get("bestBid") or row.get("yesBid") or row.get("best_bid"))
        yes_ask = _as_float(row.get("bestAsk") or row.get("yesAsk") or row.get("best_ask"))
        no_bid = _as_float(row.get("noBestBid") or row.get("noBid"))
        no_ask = _as_float(row.get("noBestAsk") or row.get("noAsk"))

        outcome_prices = row.get("outcomePrices") or row.get("outcomes")
        if isinstance(outcome_prices, str):
            try:
                import json

                outcome_prices = json.loads(outcome_prices)
            except Exception:
                outcome_prices = None
        if (yes_bid == 0.0 and yes_ask == 0.0) and outcome_prices:
            yes_price = None
            no_price = None
            if isinstance(outcome_prices, list) and len(outcome_prices) >= 2:
                yes_price = _as_float(outcome_prices[0], default=-1.0)
                no_price = _as_float(outcome_prices[1], default=-1.0)
            elif isinstance(outcome_prices, dict):
                yes_price = _as_float(outcome_prices.get("YES"), default=-1.0)
                no_price = _as_float(outcome_prices.get("NO"), default=-1.0)
            if yes_price is not None and yes_price >= 0:
                yes_bid = yes_bid or yes_price
                yes_ask = yes_ask or yes_price
            if no_price is not None and no_price >= 0:
                no_bid = no_bid or no_price
                no_ask = no_ask or no_price

        if no_bid == 0.0 and yes_ask > 0:
            no_bid = max(0.0, 1.0 - yes_ask)
        if no_ask == 0.0 and yes_bid > 0:
            no_ask = max(0.0, 1.0 - yes_bid)
        if yes_bid == 0.0 and no_ask > 0:
            yes_bid = max(0.0, 1.0 - no_ask)
        if yes_ask == 0.0 and no_bid > 0:
            yes_ask = max(0.0, 1.0 - no_bid)

        liquidity = _as_float(row.get("liquidity") or row.get("liquidityNum") or row.get("volume24hr"), default=0.0)
        top_depth = max(5.0, min(500.0, liquidity / 1000.0 if liquidity > 0 else 25.0))
        last_yes = _as_float(row.get("lastTradePrice") or row.get("lastPrice") or row.get("yesPrice"), default=yes_bid or yes_ask)
        quote_ts = _parse_dt(row.get("updatedAt") or row.get("updated_at") or row.get("lastTradeTime"))

        return MarketQuote(
            market_id=market_id,
            yes_bid=yes_bid,
            yes_ask=max(yes_ask, yes_bid),
            no_bid=no_bid,
            no_ask=max(no_ask, no_bid),
            depth_yes_top=top_depth,
            depth_no_top=top_depth,
            last_price_yes=last_yes if last_yes > 0 else None,
            as_of_utc=quote_ts,
        )

    @staticmethod
    def _extract_outcome_token_ids(row: dict[str, Any]) -> tuple[str, str]:
        # Gamma payloads vary; try common fields and formats.
        for key in ("outcomeTokenIds", "clobTokenIds", "tokenIds"):
            value = row.get(key)
            ids = PolymarketGammaWeatherMarketSource._coerce_token_id_pair(value)
            if ids is not None:
                return ids
        # Sometimes nested under metadata or tokens array
        meta = row.get("metadata")
        if isinstance(meta, dict):
            for key in ("outcomeTokenIds", "clobTokenIds", "tokenIds"):
                ids = PolymarketGammaWeatherMarketSource._coerce_token_id_pair(meta.get(key))
                if ids is not None:
                    return ids
        tokens = row.get("tokens")
        if isinstance(tokens, list):
            yes_id = ""
            no_id = ""
            for tok in tokens:
                if not isinstance(tok, dict):
                    continue
                outcome = str(tok.get("outcome") or tok.get("name") or "").strip().upper()
                tok_id = str(tok.get("tokenId") or tok.get("id") or tok.get("clobTokenId") or "")
                if not tok_id:
                    continue
                if outcome == "YES" and not yes_id:
                    yes_id = tok_id
                elif outcome == "NO" and not no_id:
                    no_id = tok_id
            if yes_id or no_id:
                return yes_id, no_id
        return "", ""

    @staticmethod
    def _coerce_token_id_pair(value: Any) -> tuple[str, str] | None:
        if value is None:
            return None
        if isinstance(value, str):
            txt = value.strip()
            if not txt:
                return None
            # JSON string list or comma-separated
            try:
                import json

                parsed = json.loads(txt)
                pair = PolymarketGammaWeatherMarketSource._coerce_token_id_pair(parsed)
                if pair is not None:
                    return pair
            except Exception:
                pass
            if "," in txt:
                parts = [p.strip() for p in txt.split(",") if p.strip()]
                if len(parts) >= 2:
                    return parts[0], parts[1]
            return None
        if isinstance(value, list):
            parts = [str(x).strip() for x in value if str(x).strip()]
            if len(parts) >= 2:
                return parts[0], parts[1]
            return None
        if isinstance(value, dict):
            yes_id = str(value.get("YES") or value.get("yes") or value.get("0") or "").strip()
            no_id = str(value.get("NO") or value.get("no") or value.get("1") or "").strip()
            if yes_id or no_id:
                return yes_id, no_id
        return None

    @staticmethod
    def _extract_city_from_meta(row: dict[str, Any]) -> str | None:
        meta = row.get("metadata")
        if isinstance(meta, dict):
            for key in ("city", "location", "marketCity"):
                val = meta.get(key)
                if isinstance(val, str) and val.strip():
                    return val.strip()
        return None

    @staticmethod
    def _extract_station_id(row: dict[str, Any], city: str) -> str:
        meta = row.get("metadata")
        if isinstance(meta, dict):
            for key in ("station", "stationId", "weatherStation"):
                val = meta.get(key)
                if isinstance(val, str) and val.strip():
                    return val.strip().upper()
        # Default guesses for common cities used in early testing.
        fallback = {"NYC": "KLGA", "Chicago": "KORD", "Seattle": "KSEA", "Atlanta": "KATL", "Dallas": "KDAL", "Miami": "KMIA"}
        return fallback.get(city, "UNKNOWN")

    @staticmethod
    def _build_resolution_notes(row: dict[str, Any]) -> str:
        fields = []
        for key in ("resolutionCriteria", "description"):
            value = row.get(key)
            if isinstance(value, str) and value.strip():
                fields.append(f"{key}={value.strip()[:240]}")
        return " | ".join(fields)

    def _in_allowed_universe(self, city: str | None, station_id: str | None, unit: str | None) -> bool:
        if city is None:
            return False
        for entry in self._cfg.allowed_universe:
            if entry.city != city:
                continue
            if unit and entry.unit and entry.unit != unit:
                continue
            if station_id and entry.station_id and station_id != entry.station_id:
                continue
            return True
        return False


def build_daily_weather_event_slugs(
    universe: tuple[WeatherCityUniverseEntry, ...],
    center_date_utc: date | None = None,
    days_before: int = 0,
    days_after: int = 1,
) -> tuple[str, ...]:
    center = center_date_utc or datetime.now(UTC).date()
    slugs: list[str] = []
    for delta in range(-days_before, days_after + 1):
        d = center + timedelta(days=delta)
        month = d.strftime("%B").lower()
        for entry in universe:
            city_slug = _city_to_slug(entry.city)
            slugs.append(f"highest-temperature-in-{city_slug}-on-{month}-{d.day}-{d.year}")
    return tuple(slugs)


def _city_to_slug(city: str) -> str:
    mapping = {
        "NYC": "nyc",
        "Atlanta": "atlanta",
        "Dallas": "dallas",
        "Chicago": "chicago",
        "Seattle": "seattle",
        "Miami": "miami",
        "Toronto": "toronto",
        "London": "london",
        "Seoul": "seoul",
        "Wellington": "wellington",
        "Buenos Aires": "buenos-aires",
    }
    return mapping.get(city, city.lower().replace(" ", "-"))
