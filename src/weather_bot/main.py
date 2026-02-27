from __future__ import annotations

import os
from dataclasses import asdict, replace
from pathlib import Path

from weather_bot.adapters.live_weather import (
    AviationWeatherMetarObservationSource,
    CityWeatherConfig,
    NwsDailyHighForecastSource,
    NwsHourlyForecastSource,
    NwsHourlyPathHighForecastSource,
)
from weather_bot.adapters.live_market import (
    PolymarketGammaConfig,
    PolymarketGammaWeatherMarketSource,
    build_daily_weather_event_slugs,
)
from weather_bot.adapters.mock_market import MockMarketSource
from weather_bot.adapters.mock_weather import MockForecastSource, MockObservationSource
from weather_bot.core.config import ScanConfig
from weather_bot.core.exits import latest_yes_mid_from_evaluations, plan_shadow_exits
from weather_bot.core.pipeline import WeatherScanPipeline
from weather_bot.core.risk import RiskEngine, portfolio_summary
from weather_bot.core.universe import get_universe
from weather_bot.execution import run_shadow_live_execution
from weather_bot.simulation.paper_journal import write_signal_event
from weather_bot.simulation.portfolio_state import (
    append_plan_log,
    apply_accepted_plans,
    append_exit_log,
    apply_accepted_exits,
    load_portfolio_state,
    save_portfolio_state,
)
from weather_bot.simulation.scan_recorder import write_scan_snapshot


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_csv_list(name: str) -> list[str] | None:
    raw = os.getenv(name)
    if raw is None:
        return None
    values = [v.strip() for v in raw.split(",") if v.strip()]
    if not values:
        return None
    return values


def _build_pipeline(cfg: ScanConfig) -> WeatherScanPipeline:
    mode = os.getenv("WEATHER_BOT_MODE", "mock").lower().strip()
    market_source = MockMarketSource()
    user_agent = os.getenv(
        "WEATHER_BOT_USER_AGENT",
        "weather-polymarket-bot/0.1 (local research contact: you@example.com)",
    )
    verify_ssl = os.getenv("WEATHER_BOT_INSECURE_SSL", "0").strip().lower() not in {"1", "true", "yes"}
    from weather_bot.utils.http import JsonHttpClient

    http_client = JsonHttpClient(user_agent=user_agent, timeout_seconds=10.0, verify_ssl=verify_ssl)

    if mode == "live_weather":
        city_configs = [
            CityWeatherConfig(city="NYC", station_id="KJFK", latitude=40.6413, longitude=-73.7781),
            CityWeatherConfig(city="Chicago", station_id="KORD", latitude=41.9742, longitude=-87.9073),
            CityWeatherConfig(city="Seattle", station_id="KSEA", latitude=47.4502, longitude=-122.3088),
        ]
        return WeatherScanPipeline(
            market_source=market_source,
            forecast_sources=[
                NwsDailyHighForecastSource(city_configs=city_configs, http_client=http_client),
                NwsHourlyPathHighForecastSource(city_configs=city_configs, http_client=http_client),
                NwsHourlyForecastSource(city_configs=city_configs, http_client=http_client),
            ],
            observation_source=AviationWeatherMetarObservationSource(http_client=http_client),
            config=cfg,
        )
    if mode == "live_scan":
        universe_level = os.getenv("WEATHER_BOT_UNIVERSE", "tier1")
        universe = get_universe(universe_level)
        event_slugs = build_daily_weather_event_slugs(universe, days_before=0, days_after=1)
        city_configs = [
            CityWeatherConfig(city="NYC", station_id="KLGA", latitude=40.7769, longitude=-73.8740),
            CityWeatherConfig(city="Chicago", station_id="KORD", latitude=41.9742, longitude=-87.9073),
            CityWeatherConfig(city="Seattle", station_id="KSEA", latitude=47.4502, longitude=-122.3088),
            CityWeatherConfig(city="Atlanta", station_id="KATL", latitude=33.6367, longitude=-84.4281),
            CityWeatherConfig(city="Dallas", station_id="KDFW", latitude=32.8998, longitude=-97.0403),
            CityWeatherConfig(city="Miami", station_id="KMIA", latitude=25.7959, longitude=-80.2870),
        ]
        if any(entry.station_id == "KDAL" for entry in universe):
            city_configs.append(CityWeatherConfig(city="Dallas", station_id="KDAL", latitude=32.8471, longitude=-96.8517))
        return WeatherScanPipeline(
            market_source=PolymarketGammaWeatherMarketSource(
                http_client=http_client,
                config=PolymarketGammaConfig(
                    allowed_universe=universe,
                    require_wunderground_rules=True,
                    use_event_slug_discovery=True,
                    event_slugs=event_slugs,
                ),
            ),
            forecast_sources=[
                NwsDailyHighForecastSource(city_configs=city_configs, http_client=http_client),
                NwsHourlyPathHighForecastSource(city_configs=city_configs, http_client=http_client),
                NwsHourlyForecastSource(city_configs=city_configs, http_client=http_client),
            ],
            observation_source=AviationWeatherMetarObservationSource(http_client=http_client),
            config=cfg,
        )

    return WeatherScanPipeline(
        market_source=market_source,
        forecast_sources=[
            MockForecastSource(name="mock-noaa", temp_bias=0.0, range_width=4.0),
            MockForecastSource(name="mock-nbm", temp_bias=0.6, range_width=3.5),
            MockForecastSource(name="mock-hrrr", temp_bias=-0.4, range_width=5.0),
        ],
        observation_source=MockObservationSource(),
        config=cfg,
    )


def main() -> int:
    base_cfg = ScanConfig()
    city_override = _env_csv_list("WEATHER_BOT_CITIES")
    cfg = replace(
        base_cfg,
        cities=city_override if city_override else base_cfg.cities,
        allow_buy_yes=_env_bool("WEATHER_BOT_ALLOW_BUY_YES", base_cfg.allow_buy_yes),
        allow_buy_no=_env_bool("WEATHER_BOT_ALLOW_BUY_NO", base_cfg.allow_buy_no),
        min_yes_price_for_buy_yes=_env_float("WEATHER_BOT_MIN_YES_PRICE_FOR_BUY_YES", base_cfg.min_yes_price_for_buy_yes),
        min_no_price_for_buy_no=_env_float("WEATHER_BOT_MIN_NO_PRICE_FOR_BUY_NO", base_cfg.min_no_price_for_buy_no),
        max_no_price_for_buy_no=_env_float("WEATHER_BOT_MAX_NO_PRICE_FOR_BUY_NO", base_cfg.max_no_price_for_buy_no),
        min_edge_buy_yes=_env_float("WEATHER_BOT_MIN_EDGE_BUY_YES", base_cfg.min_edge_buy_yes),
        min_edge_buy_no=_env_float("WEATHER_BOT_MIN_EDGE_BUY_NO", base_cfg.min_edge_buy_no),
        min_hours_to_target=_env_float("WEATHER_BOT_MIN_HOURS_TO_TARGET", base_cfg.min_hours_to_target),
        max_hours_to_target=_env_float("WEATHER_BOT_MAX_HOURS_TO_TARGET", base_cfg.max_hours_to_target),
    )
    mode = os.getenv("WEATHER_BOT_MODE", "mock").lower().strip()
    pipeline = _build_pipeline(cfg)

    result = pipeline.run_scan()
    print(f"Scanned at: {result.scanned_at_utc.isoformat()}")
    print(f"Opportunities: {len(result.opportunities)}")
    for idx, opp in enumerate(result.opportunities, start=1):
        direction = "BUY YES" if opp.edge > 0 else "BUY NO"
        print(
            f"{idx}. {opp.market.market_id} {direction} "
            f"edge={opp.edge:+.3f} conf={opp.confidence_score:.3f} "
            f"mid={opp.implied_yes_mid:.3f} model={opp.model_prob_yes:.3f}"
        )
        for reason in opp.reasons:
            print(f"   - {reason}")

    if result.opportunities:
        write_signal_event(
            path=Path("data/sample/paper_journal.jsonl"),
            opportunity=result.opportunities[0],
            size_usd=cfg.paper_trade_size_usd,
            strategy_id=cfg.strategy_id,
        )
        print("Wrote one paper signal event to data/sample/paper_journal.jsonl")

    if os.getenv("WEATHER_BOT_RECORD_SCAN", "0").strip().lower() in {"1", "true", "yes"}:
        scan_path = Path(os.getenv("WEATHER_BOT_SCAN_RECORD_PATH", "data/sample/scan_snapshots.jsonl"))
        write_scan_snapshot(
            path=scan_path,
            result=result,
            mode=mode,
            config=asdict(cfg),
            strategy_id=cfg.strategy_id,
            run_meta={
                "run_id": os.getenv("WEATHER_BOT_RUN_ID", ""),
                "scan_seq": os.getenv("WEATHER_BOT_SCAN_SEQ", ""),
            },
        )
        print(f"Wrote scan snapshot to {scan_path}")

    if os.getenv("WEATHER_BOT_SHADOW_EXECUTE", "0").strip().lower() in {"1", "true", "yes"}:
        state_path = Path(os.getenv("WEATHER_BOT_PORTFOLIO_STATE_PATH", "data/sample/portfolio_state.json"))
        plan_log_path = Path(os.getenv("WEATHER_BOT_PLAN_LOG_PATH", "data/sample/planned_orders.jsonl"))
        state = load_portfolio_state(state_path)
        risk_engine = RiskEngine(cfg)
        plans = risk_engine.plan_orders(result.opportunities, state)
        append_plan_log(plan_log_path, plans, mode=mode, strategy_id=cfg.strategy_id)
        next_state = apply_accepted_plans(state, plans)
        save_portfolio_state(state_path, next_state)
        accepted = sum(1 for p in plans if p.accepted)
        print(f"Shadow execution planned {len(plans)} orders ({accepted} accepted).")
        print(f"Plan log: {plan_log_path}")
        print(f"Portfolio state: {state_path}")
        print(f"Portfolio summary: {portfolio_summary(next_state)}")

    exec_mode = os.getenv("WEATHER_BOT_EXECUTION_MODE", "off").strip().lower()
    if exec_mode in {"shadow_submit", "dry_run", "live_canary"}:
        exec_state = run_shadow_live_execution(
            opportunities=result.opportunities,
            scan_time_utc=result.scanned_at_utc,
            cfg=cfg,
            mode=mode,
        )
        print(
            f"{exec_mode} execution attempts="
            f"{exec_state.get('attempts_this_scan')} accepted={exec_state.get('accepted_shadow_submits_this_scan')} "
            f"guard={((exec_state.get('guard') or {}).get('reason'))}"
        )
        print(f"Exec state: {os.getenv('WEATHER_BOT_EXEC_STATE_PATH', 'data/sample/live_execution_state.json')}")

    if os.getenv("WEATHER_BOT_SHADOW_EXITS", "0").strip().lower() in {"1", "true", "yes"}:
        state_path = Path(os.getenv("WEATHER_BOT_PORTFOLIO_STATE_PATH", "data/sample/portfolio_state.json"))
        exit_log_path = Path(os.getenv("WEATHER_BOT_EXIT_LOG_PATH", "data/sample/planned_exits.jsonl"))
        state = load_portfolio_state(state_path)
        exits = plan_shadow_exits(state, latest_yes_mid_from_evaluations(result.evaluations), cfg)
        append_exit_log(exit_log_path, exits, mode=mode, strategy_id=cfg.strategy_id)
        next_state = apply_accepted_exits(state, exits)
        save_portfolio_state(state_path, next_state)
        accepted_exits = sum(1 for e in exits if e.accepted)
        print(f"Shadow exits planned {len(exits)} positions ({accepted_exits} accepted).")
        print(f"Exit log: {exit_log_path}")
        print(f"Portfolio state: {state_path}")
        print(f"Portfolio summary: {portfolio_summary(next_state)}")

    if result.skipped_markets:
        print("Skipped:")
        for msg in result.skipped_markets:
            print(f" - {msg}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
