from __future__ import annotations

from datetime import datetime, timedelta, timezone

from weather_bot.core.interfaces import MarketSource
from weather_bot.models.domain import MarketQuote, WeatherMarket


class MockMarketSource(MarketSource):
    name = "mock-polymarket"

    def __init__(self) -> None:
        target = (datetime.now(timezone.utc) + timedelta(hours=18)).replace(minute=0, second=0, microsecond=0)
        self._markets = [
            WeatherMarket(
                market_id="wx_nyc_72_74",
                city="NYC",
                station_id="KJFK",
                target_time_utc=target,
                bucket_low_f=72.0,
                bucket_high_f=74.0,
                resolution_notes="Daily high bucket demo",
            ),
            WeatherMarket(
                market_id="wx_chi_66_68",
                city="Chicago",
                station_id="KORD",
                target_time_utc=target,
                bucket_low_f=66.0,
                bucket_high_f=68.0,
                resolution_notes="Daily high bucket demo",
            ),
            WeatherMarket(
                market_id="wx_sea_60_62",
                city="Seattle",
                station_id="KSEA",
                target_time_utc=target,
                bucket_low_f=60.0,
                bucket_high_f=62.0,
                resolution_notes="Daily high bucket demo",
            ),
        ]

    def list_markets(self) -> list[WeatherMarket]:
        return list(self._markets)

    def fetch_quote(self, market_id: str) -> MarketQuote:
        quote_map = {
            "wx_nyc_72_74": (0.18, 0.22, 0.78, 0.83, 120.0, 140.0, 0.20),
            "wx_chi_66_68": (0.31, 0.37, 0.62, 0.68, 45.0, 60.0, 0.34),
            "wx_sea_60_62": (0.09, 0.14, 0.85, 0.91, 30.0, 33.0, 0.11),
        }
        yb, ya, nb, na, dy, dn, last = quote_map[market_id]
        return MarketQuote(
            market_id=market_id,
            yes_bid=yb,
            yes_ask=ya,
            no_bid=nb,
            no_ask=na,
            depth_yes_top=dy,
            depth_no_top=dn,
            last_price_yes=last,
            as_of_utc=datetime.now(timezone.utc),
        )
