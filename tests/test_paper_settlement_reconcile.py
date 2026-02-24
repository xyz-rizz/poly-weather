from __future__ import annotations

from datetime import UTC, datetime, timedelta

from weather_bot.paper_settlement_reconcile import (
    PaperSignalPosition,
    _mark_exit_price_and_reason,
)


def test_mark_exit_take_profit_buy_yes():
    now = datetime.now(UTC)
    pos = PaperSignalPosition(
        market_id="m1",
        event_slug="e1",
        city="Atlanta",
        direction="BUY_YES",
        size_usd=5.0,
        entry_price=0.20,
        signal_time_utc=now - timedelta(hours=1),
        target_time_utc=now + timedelta(hours=2),
    )
    row = {
        "implied_yes_mid": 0.32,
        "yes_bid": 0.31,
        "yes_ask": 0.33,
        "quote_time_utc": now.isoformat(),
        "target_time_utc": (now + timedelta(hours=2)).isoformat(),
    }
    decision = _mark_exit_price_and_reason(
        pos,
        row,
        now_utc=now,
        mark_fresh_seconds=1800,
        take_profit_pct=0.35,
        stop_loss_pct=0.20,
        time_stop_grace_seconds=300,
    )
    assert decision is not None
    exit_price, reason, ret_mark, _ = decision
    assert reason == "take_profit"
    assert round(exit_price, 6) == 0.31  # uses conservative bid for exit
    assert ret_mark > 0.35  # trigger checks mark-mid return


def test_mark_exit_time_stop_after_target_grace():
    now = datetime.now(UTC)
    pos = PaperSignalPosition(
        market_id="m2",
        event_slug="e2",
        city="Dallas",
        direction="BUY_NO",
        size_usd=5.0,
        entry_price=0.45,
        signal_time_utc=now - timedelta(hours=4),
        target_time_utc=now - timedelta(minutes=10),
    )
    row = {
        "implied_yes_mid": 0.40,  # BUY_NO position mark = 0.60, no TP/SL required
        "no_bid": 0.58,
        "no_ask": 0.60,
        "quote_time_utc": now.isoformat(),
    }
    decision = _mark_exit_price_and_reason(
        pos,
        row,
        now_utc=now,
        mark_fresh_seconds=1800,
        take_profit_pct=0.50,
        stop_loss_pct=0.50,
        time_stop_grace_seconds=300,
    )
    assert decision is not None
    exit_price, reason, _, _ = decision
    assert reason == "time_stop"
    assert round(exit_price, 6) == 0.58  # conservative no-bid fill for BUY_NO

