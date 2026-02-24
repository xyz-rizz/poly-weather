from __future__ import annotations

from datetime import UTC, datetime, timedelta

from weather_bot.paper_settlement_reconcile import (
    PaperSignalPosition,
    _load_exit_regime_profile,
    _mark_exit_price_and_reason,
    _select_exit_regime,
)


def test_mark_exit_take_profit_buy_yes():
    now = datetime.now(UTC)
    pos = PaperSignalPosition(
        market_id="m1",
        event_slug="e1",
        city="Atlanta",
        direction="BUY_YES",
        size_usd=5.0,
        original_size_usd=5.0,
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
        regime=_select_exit_regime(
            pos,
            now_utc=now,
            profile={"all": {"all": {"take_profit_pct": 0.35, "stop_loss_pct": 0.20, "time_stop_grace_seconds": 300, "mark_fresh_seconds": 1800}}},
        ),
        core_break_even_enabled=True,
        core_break_even_buffer_pct=0.02,
        core_trailing_enabled=True,
        core_trailing_drawdown_pct=0.15,
        core_trailing_min_peak_return_pct=0.25,
        min_hold_minutes_before_stop_loss=0,
        max_spread_for_stop_loss=1.0,
        max_spread_for_take_profit=1.0,
    )
    assert decision is not None
    exit_price, reason, ret_mark, _, ret_exec, _ = decision
    assert reason == "take_profit"
    assert round(exit_price, 6) == 0.31  # uses conservative bid for exit
    assert ret_mark > 0.35  # trigger checks mark-mid return
    assert ret_exec > 0.35


def test_mark_exit_time_stop_after_target_grace():
    now = datetime.now(UTC)
    pos = PaperSignalPosition(
        market_id="m2",
        event_slug="e2",
        city="Dallas",
        direction="BUY_NO",
        size_usd=5.0,
        original_size_usd=5.0,
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
        regime=_select_exit_regime(
            pos,
            now_utc=now,
            profile={"all": {"all": {"take_profit_pct": 0.50, "stop_loss_pct": 0.50, "time_stop_grace_seconds": 300, "mark_fresh_seconds": 1800}}},
        ),
        core_break_even_enabled=True,
        core_break_even_buffer_pct=0.02,
        core_trailing_enabled=True,
        core_trailing_drawdown_pct=0.15,
        core_trailing_min_peak_return_pct=0.25,
        min_hold_minutes_before_stop_loss=0,
        max_spread_for_stop_loss=1.0,
        max_spread_for_take_profit=1.0,
    )
    assert decision is not None
    exit_price, reason, _, _, _, _ = decision
    assert reason == "time_stop"
    assert round(exit_price, 6) == 0.58  # conservative no-bid fill for BUY_NO


def test_select_exit_regime_city_and_horizon_override(tmp_path, monkeypatch):
    profile_path = tmp_path / "exit_regimes.json"
    profile_path.write_text(
        """
{
  "global": {"take_profit_pct": 0.35, "stop_loss_pct": 0.20, "time_stop_grace_seconds": 300, "mark_fresh_seconds": 1800},
  "cities": {
    "Atlanta": {
      "take_profit_pct": 0.30,
      "horizons": {
        "0-2h": {"take_profit_pct": 0.18, "stop_loss_pct": 0.12, "mark_fresh_seconds": 600}
      }
    }
  }
}
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.setenv("WEATHER_BOT_PAPER_EXIT_REGIME_PROFILE_PATH", str(profile_path))
    now = datetime.now(UTC)
    pos = PaperSignalPosition(
        market_id="m",
        event_slug="e",
        city="Atlanta",
        direction="BUY_YES",
        size_usd=5.0,
        original_size_usd=5.0,
        entry_price=0.2,
        signal_time_utc=now - timedelta(hours=1),
        target_time_utc=now + timedelta(minutes=45),
    )
    profile = _load_exit_regime_profile(
        base_dir=tmp_path,
        default_take_profit_pct=0.35,
        default_stop_loss_pct=0.2,
        default_time_stop_grace_seconds=300,
        default_mark_fresh_seconds=1800,
    )
    regime = _select_exit_regime(pos, now_utc=now, profile=profile)
    assert regime.city_key == "Atlanta"
    assert regime.horizon_key == "0-2h"
    assert regime.take_profit_pct == 0.18
    assert regime.stop_loss_pct == 0.12
    assert regime.mark_fresh_seconds == 600.0


def test_mark_exit_break_even_after_partial_tp():
    now = datetime.now(UTC)
    pos = PaperSignalPosition(
        market_id="m3",
        event_slug="e3",
        city="NYC",
        direction="BUY_YES",
        size_usd=2.5,
        original_size_usd=5.0,
        entry_price=0.20,
        signal_time_utc=now - timedelta(hours=3),
        target_time_utc=now + timedelta(hours=6),
        partial_tp_taken=True,
        peak_mark_return_pct=0.40,
    )
    row = {
        "implied_yes_mid": 0.203,  # +1.5% return on position price
        "yes_bid": 0.202,
        "yes_ask": 0.204,
        "quote_time_utc": now.isoformat(),
    }
    decision = _mark_exit_price_and_reason(
        pos,
        row,
        now_utc=now,
        regime=_select_exit_regime(
            pos,
            now_utc=now,
            profile={"all": {"all": {"take_profit_pct": 0.50, "stop_loss_pct": 0.50, "time_stop_grace_seconds": 300, "mark_fresh_seconds": 1800}}},
        ),
        core_break_even_enabled=True,
        core_break_even_buffer_pct=0.02,
        core_trailing_enabled=False,
        core_trailing_drawdown_pct=0.15,
        core_trailing_min_peak_return_pct=0.25,
        min_hold_minutes_before_stop_loss=0,
        max_spread_for_stop_loss=1.0,
        max_spread_for_take_profit=1.0,
    )
    assert decision is not None
    _, reason, _, _, _, _ = decision
    assert reason == "break_even_stop"


def test_mark_exit_trailing_after_partial_tp():
    now = datetime.now(UTC)
    pos = PaperSignalPosition(
        market_id="m4",
        event_slug="e4",
        city="Dallas",
        direction="BUY_NO",
        size_usd=2.5,
        original_size_usd=5.0,
        entry_price=0.40,
        signal_time_utc=now - timedelta(hours=2),
        target_time_utc=now + timedelta(hours=4),
        partial_tp_taken=True,
        peak_mark_return_pct=0.55,
    )
    # BUY_NO mark position price = 1 - yes_mid = 0.50 => return = +25%
    row = {
        "implied_yes_mid": 0.50,
        "no_bid": 0.49,
        "no_ask": 0.51,
        "quote_time_utc": now.isoformat(),
    }
    decision = _mark_exit_price_and_reason(
        pos,
        row,
        now_utc=now,
        regime=_select_exit_regime(
            pos,
            now_utc=now,
            profile={"all": {"all": {"take_profit_pct": 0.80, "stop_loss_pct": 0.80, "time_stop_grace_seconds": 300, "mark_fresh_seconds": 1800}}},
        ),
        core_break_even_enabled=True,
        core_break_even_buffer_pct=0.02,
        core_trailing_enabled=True,
        core_trailing_drawdown_pct=0.15,
        core_trailing_min_peak_return_pct=0.25,
        min_hold_minutes_before_stop_loss=0,
        max_spread_for_stop_loss=1.0,
        max_spread_for_take_profit=1.0,
    )
    assert decision is not None
    _, reason, _, _, _, _ = decision
    assert reason == "trailing_stop"


def test_stop_loss_requires_min_hold_and_respects_spread():
    now = datetime.now(UTC)
    pos = PaperSignalPosition(
        market_id="m5",
        event_slug="e5",
        city="Seattle",
        direction="BUY_YES",
        size_usd=5.0,
        original_size_usd=5.0,
        entry_price=0.4,
        signal_time_utc=now - timedelta(minutes=5),
        target_time_utc=now + timedelta(hours=3),
    )
    row = {
        "implied_yes_mid": 0.28,
        "yes_bid": 0.20,
        "yes_ask": 0.36,  # wide spread
        "quote_time_utc": now.isoformat(),
    }
    reg = _select_exit_regime(
        pos,
        now_utc=now,
        profile={"all": {"all": {"take_profit_pct": 0.8, "stop_loss_pct": 0.2, "time_stop_grace_seconds": 300, "mark_fresh_seconds": 1800}}},
    )
    decision = _mark_exit_price_and_reason(
        pos,
        row,
        now_utc=now,
        regime=reg,
        core_break_even_enabled=True,
        core_break_even_buffer_pct=0.02,
        core_trailing_enabled=True,
        core_trailing_drawdown_pct=0.15,
        core_trailing_min_peak_return_pct=0.25,
        min_hold_minutes_before_stop_loss=20,
        max_spread_for_stop_loss=0.12,
        max_spread_for_take_profit=1.0,
    )
    assert decision is None
