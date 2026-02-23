from pathlib import Path

from weather_bot.health import check_health


def test_health_with_no_logs(tmp_path: Path) -> None:
    out = check_health(tmp_path)
    assert out["status"] == "OK"
    assert out["scan_count"] == 0
