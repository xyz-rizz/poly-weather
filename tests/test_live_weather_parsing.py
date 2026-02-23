from datetime import UTC, datetime

from weather_bot.adapters.live_weather import _best_period_for_target, _to_f


def test_to_f_converts_celsius() -> None:
    assert round(_to_f(20, "C"), 1) == 68.0
    assert round(_to_f(68, "F"), 1) == 68.0


def test_best_period_selects_containing_window() -> None:
    target = datetime(2026, 2, 24, 12, 30, tzinfo=UTC)
    periods = [
        {"startTime": "2026-02-24T11:00:00+00:00", "endTime": "2026-02-24T12:00:00+00:00"},
        {"startTime": "2026-02-24T12:00:00+00:00", "endTime": "2026-02-24T13:00:00+00:00", "temperature": 70},
        {"startTime": "2026-02-24T13:00:00+00:00", "endTime": "2026-02-24T14:00:00+00:00"},
    ]
    match = _best_period_for_target(periods, target)
    assert match is not None
    assert match["temperature"] == 70
