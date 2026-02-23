import json
from pathlib import Path

from weather_bot.reconcile import reconcile_portfolio


def test_reconcile_handles_empty_state(tmp_path: Path) -> None:
    (tmp_path / "portfolio_state.json").write_text(
        json.dumps({"as_of_utc": None, "realized_pnl_today_usd": 0.0, "open_positions": []}),
        encoding="utf-8",
    )
    (tmp_path / "scan_snapshots.jsonl").write_text("", encoding="utf-8")
    out = reconcile_portfolio(tmp_path)
    assert out["summary"]["open_positions"] == 0
    assert out["summary"]["unrealized_pnl_usd"] == 0.0
