from datetime import UTC

from weather_bot.adapters.live_market import PolymarketGammaWeatherMarketSource
from weather_bot.utils.http import JsonHttpClient


class _StubHttp(JsonHttpClient):
    def __init__(self, payload):
        super().__init__()
        self.payload = payload

    def get_json(self, url: str, headers=None):
        return self.payload


def test_gamma_market_parser_builds_weather_market_and_quote() -> None:
    payload = [
        {
            "id": "123",
            "question": "Will NYC temperature be between 72 and 74 F on Feb 24?",
            "description": "Resolution based on Wunderground weather station data (KLGA). Ends 2026-02-24 11PM ET.",
            "category": "Weather",
            "endDate": "2026-02-25T04:00:00Z",
            "bestBid": "0.21",
            "bestAsk": "0.25",
            "liquidityNum": "25000",
            "updatedAt": "2026-02-23T12:00:00Z",
            "resolutionCriteria": "According to Wunderground at KLGA",
            "metadata": {"stationId": "KLGA"},
        }
    ]
    source = PolymarketGammaWeatherMarketSource(http_client=_StubHttp(payload))
    markets = source.list_markets()
    assert len(markets) == 1
    market = markets[0]
    assert market.city == "NYC"
    assert market.station_id == "KLGA"
    assert market.bucket_low_f == 72.0
    assert market.bucket_high_f == 74.0
    assert market.target_time_utc.tzinfo == UTC
    quote = source.fetch_quote(market.market_id)
    assert quote.yes_bid == 0.21
    assert quote.yes_ask == 0.25
