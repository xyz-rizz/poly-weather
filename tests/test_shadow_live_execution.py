from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from weather_bot.core.config import ScanConfig
from weather_bot.execution.shadow_live import run_shadow_live_execution
from weather_bot.models.domain import MarketQuote, Opportunity, WeatherMarket


def _opp(now: datetime) -> Opportunity:
    market = WeatherMarket(
        market_id="m1",
        city="Atlanta",
        station_id="KATL",
        target_time_utc=now + timedelta(hours=12),
        bucket_low_f=70,
        bucket_high_f=71,
        event_slug="highest-temperature-in-atlanta-on-february-24-2026",
        yes_token_id="1001",
        no_token_id="1002",
        clob_market_id="cond-1",
    )
    quote = MarketQuote(
        market_id="m1",
        yes_bid=0.24,
        yes_ask=0.26,
        no_bid=0.74,
        no_ask=0.76,
        depth_yes_top=100,
        depth_no_top=100,
        last_price_yes=0.25,
        as_of_utc=now,
    )
    return Opportunity(
        market=market,
        quote=quote,
        implied_yes_mid=0.25,
        model_prob_yes=0.37,
        edge=0.12,
        confidence_score=0.8,
        liquidity_score=0.8,
        uncertainty_score=0.7,
        reasons=["test"],
    )


def test_shadow_live_execution_guard_disabled(tmp_path, monkeypatch):
    now = datetime.now(UTC)
    monkeypatch.setenv("WEATHER_BOT_RUNNER_BASEDIR", str(tmp_path))
    monkeypatch.setenv("WEATHER_BOT_EXECUTION_MODE", "shadow_submit")
    monkeypatch.setenv("WEATHER_BOT_EXEC_ALLOW", "0")
    result = run_shadow_live_execution(opportunities=[_opp(now)], scan_time_utc=now, cfg=ScanConfig(), mode="live_scan")
    assert result["guard"]["allowed"] is False
    assert result["accepted_shadow_submits_this_scan"] == 0
    assert Path(tmp_path / "live_execution_attempts.jsonl").exists()


def test_shadow_live_execution_accepts_when_guards_met(tmp_path, monkeypatch):
    now = datetime.now(UTC)
    monkeypatch.setenv("WEATHER_BOT_RUNNER_BASEDIR", str(tmp_path))
    monkeypatch.setenv("WEATHER_BOT_EXEC_ALLOW", "1")
    monkeypatch.setenv("WEATHER_BOT_EXEC_MIN_CLOSED_TRADES", "0")
    monkeypatch.setenv("WEATHER_BOT_EXEC_MIN_TOTAL_PNL_USD", "-100")
    monkeypatch.setenv("WEATHER_BOT_EXEC_MAX_SUBMITS_PER_SCAN", "1")
    monkeypatch.setenv("WEATHER_BOT_EXEC_MAX_SCAN_AGE_SECONDS", "600")
    (tmp_path / "paper_performance_report.json").write_text(
        json.dumps({"closed_summary": {"trades": 0}, "open_summary": {"total_pnl_including_open_usd": 0.0}}),
        encoding="utf-8",
    )
    (tmp_path / "paper_settlement_report.json").write_text(json.dumps({"realized_pnl_total_usd": 0.0}), encoding="utf-8")
    (tmp_path / "paper_settlement_state.json").write_text(json.dumps({"open_positions": []}), encoding="utf-8")
    (tmp_path / "portfolio_state.json").write_text(json.dumps({"open_positions": [], "realized_pnl_today_usd": 0.0}), encoding="utf-8")
    result = run_shadow_live_execution(opportunities=[_opp(now)], scan_time_utc=now, cfg=ScanConfig(), mode="live_scan")
    assert result["guard"]["allowed"] is True
    assert result["accepted_shadow_submits_this_scan"] == 1
    rows = (tmp_path / "live_execution_attempts.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(rows) == 1
    first = json.loads(rows[0])
    assert first["accepted"] is True
    assert first["intent"]["payload"]["shadow_only"] is True


def test_dry_run_mode_writes_submit_result(tmp_path, monkeypatch):
    now = datetime.now(UTC)
    monkeypatch.setenv("WEATHER_BOT_RUNNER_BASEDIR", str(tmp_path))
    monkeypatch.setenv("WEATHER_BOT_EXEC_ALLOW", "1")
    monkeypatch.setenv("WEATHER_BOT_EXECUTION_MODE", "dry_run")
    monkeypatch.setenv("WEATHER_BOT_EXEC_MIN_CLOSED_TRADES", "0")
    monkeypatch.setenv("WEATHER_BOT_EXEC_MIN_TOTAL_PNL_USD", "-100")
    monkeypatch.setenv("WEATHER_BOT_EXEC_MAX_SCAN_AGE_SECONDS", "600")
    (tmp_path / "paper_performance_report.json").write_text(
        json.dumps({"closed_summary": {"trades": 0}, "open_summary": {"total_pnl_including_open_usd": 0.0}}),
        encoding="utf-8",
    )
    (tmp_path / "paper_settlement_report.json").write_text(json.dumps({"realized_pnl_total_usd": 0.0}), encoding="utf-8")
    (tmp_path / "paper_settlement_state.json").write_text(json.dumps({"open_positions": []}), encoding="utf-8")
    (tmp_path / "portfolio_state.json").write_text(json.dumps({"open_positions": [], "realized_pnl_today_usd": 0.0}), encoding="utf-8")
    result = run_shadow_live_execution(opportunities=[_opp(now)], scan_time_utc=now, cfg=ScanConfig(), mode="live_scan")
    assert result["execution_mode"] == "dry_run"
    assert result["accepted_shadow_submits_this_scan"] == 1
    assert result["submit_results_this_scan"] == 1
    lines = (tmp_path / "live_execution_results.jsonl").read_text(encoding="utf-8").splitlines()
    obj = json.loads(lines[-1])
    assert obj["submit_ok"] is True
