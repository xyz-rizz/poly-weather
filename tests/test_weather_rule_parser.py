from weather_bot.adapters.weather_rule_parser import parse_weather_market_rule


def test_parse_us_range_bucket_wunderground_station() -> None:
    row = {
        "question": "Highest temperature in Atlanta on February 24?",
        "description": "This market resolves to the highest temperature recorded at KATL according to Wunderground. 40-41F bucket.",
        "resolutionCriteria": "Resolution source: Wunderground station KATL.",
        "metadata": {"stationId": "KATL"},
    }
    parsed = parse_weather_market_rule(row)
    assert parsed is not None
    assert parsed.city == "Atlanta"
    assert parsed.station_id == "KATL"
    assert parsed.unit == "F"
    assert parsed.bucket_low == 40.0
    assert parsed.bucket_high == 41.0
    assert parsed.settlement_source == "Wunderground"
    assert parsed.bucket_kind == "range"


def test_parse_tail_bucket_celsius() -> None:
    row = {
        "question": "Highest temperature in Toronto on February 23?",
        "description": "Resolves using Wunderground data for CYYZ. -1C or below bucket.",
        "resolutionCriteria": "CYYZ weather station from Wunderground.",
    }
    parsed = parse_weather_market_rule(row)
    assert parsed is not None
    assert parsed.city == "Toronto"
    assert parsed.station_id == "CYYZ"
    assert parsed.unit == "C"
    assert parsed.bucket_kind == "upper_tail"
    assert parsed.bucket_high == -1.0
