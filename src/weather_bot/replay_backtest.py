from __future__ import annotations

import json
import os
from pathlib import Path

from weather_bot.simulation.backtest import run_replay_backtest


def main() -> int:
    base_dir = Path(os.getenv("WEATHER_BOT_RUNNER_BASEDIR", "data/sample"))
    out = run_replay_backtest(
        base_dir,
        strategy_id=os.getenv("WEATHER_BOT_STRATEGY_ID") or None,
        scan_mode=os.getenv("WEATHER_BOT_SCAN_MODE", "live_scan") or None,
        size_usd=float(os.getenv("WEATHER_BOT_BT_SIZE_USD", "5")),
        take_profit_pct=float(os.getenv("WEATHER_BOT_BT_TP_PCT", "0.35")),
        stop_loss_pct=float(os.getenv("WEATHER_BOT_BT_SL_PCT", "0.20")),
        max_positions_per_event=int(os.getenv("WEATHER_BOT_BT_MAX_POS_EVENT", "1")),
        min_entry_price=float(os.getenv("WEATHER_BOT_BT_MIN_ENTRY_PRICE", "0.03")),
        slippage_bps=float(os.getenv("WEATHER_BOT_BT_SLIPPAGE_BPS", "50")),
        settle_with_gamma=os.getenv("WEATHER_BOT_BT_SETTLE_WITH_GAMMA", "0").strip().lower() in {"1", "true", "yes"},
        universe_level=os.getenv("WEATHER_BOT_UNIVERSE", "tier1"),
        days_back=int(os.getenv("WEATHER_BOT_CAL_DAYS_BACK", "30")),
        insecure_ssl=os.getenv("WEATHER_BOT_INSECURE_SSL", "0").strip().lower() in {"1", "true", "yes"},
    )
    print(json.dumps(out, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
