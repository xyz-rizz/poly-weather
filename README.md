# Weather Polymarket Bot (Phase 1 Scaffold)

This project starts with a research-first, paper-trading-first architecture for a non-crypto prediction market bot focused on weather markets.

It is intentionally built around:

- multi-source weather intelligence (not NOAA-only)
- exact resolution logic awareness
- mispricing scoring with uncertainty and liquidity checks
- paper-trading journaling before live execution
- modular adapters so real APIs can be added incrementally

## What is implemented now

- Core data models for forecasts, observations, market quotes, and opportunities
- Pluggable source interfaces (`weather`, `observation`, `market`)
- Scoring engine (consensus, disagreement, liquidity penalty, confidence)
- Offline mock adapters to simulate a scan
- Paper trade journal writer (JSONL)
- Runnable CLI entrypoint to generate ranked opportunities
- Expanded roadmap and architecture notes in `docs/WEATHER_BOT_ROADMAP.md`

## Quick start

```bash
PYTHONPATH=src python3 -m weather_bot
```

This runs a mock scan and writes a paper journal event into:

- `data/sample/paper_journal.jsonl`

## Live weather integrations (still mock market quotes)

This repo now includes:

- `NWS hourly forecast` adapter (`api.weather.gov`)
- `AviationWeather METAR` observation adapter

To run with live weather data while still using mock market quotes:

```bash
WEATHER_BOT_MODE=live_weather \
WEATHER_BOT_USER_AGENT="weather-polymarket-bot/0.1 (contact: your-email@example.com)" \
PYTHONPATH=src python3 -m weather_bot
```

Notes:

- NWS requests should include a clear `User-Agent`.
- Network access must be available from your environment.
- Polymarket market integration is still pending (next step).

## Live scan mode (weather + Polymarket market parser scaffold)

`live_scan` mode uses:

- NWS hourly forecasts
- AviationWeather METAR observations
- Polymarket Gamma weather-market parsing scaffold (best-effort normalization)

```bash
WEATHER_BOT_MODE=live_scan \
WEATHER_BOT_USER_AGENT="weather-polymarket-bot/0.1 (contact: your-email@example.com)" \
WEATHER_BOT_RECORD_SCAN=1 \
PYTHONPATH=src python3 -m weather_bot
```

Notes:

- This market adapter is intentionally tolerant and schema-agnostic because Polymarket/Gamma payloads vary.
- It is a starting integration for discovery/scanning, not execution.
- You should validate parsed `city`, `bucket`, and `resolution_notes` before using any signals.

## Shadow Execution (Risk-Gated Paper Intents)

You can now turn ranked opportunities into risk-screened paper order plans and persist a simple portfolio state:

```bash
WEATHER_BOT_SHADOW_EXECUTE=1 \
WEATHER_BOT_RECORD_SCAN=1 \
PYTHONPATH=src python3 -m weather_bot
```

Outputs:

- `data/sample/planned_orders.jsonl`
- `data/sample/planned_exits.jsonl` (if `WEATHER_BOT_SHADOW_EXITS=1`)
- `data/sample/portfolio_state.json`
- `data/sample/scan_snapshots.jsonl` (if enabled)

This is still paper/shadow mode. No live orders are placed.

Shadow exit planning (take-profit / stop-loss on current scan marks):

```bash
WEATHER_BOT_SHADOW_EXITS=1 \
PYTHONPATH=src python3 -m weather_bot
```

## Local Reporting / Replay Prep

Summarize recorded scans, planned orders, and current portfolio state:

```bash
PYTHONPATH=src python3 -m weather_bot.report
```

This gives you quick feedback on:

- opportunity frequency
- skip frequency
- accepted vs rejected plans
- top reject reasons
- city concentration in paper positions

## Replay / Reconcile / Health

Replay-oriented summary from recorded scans + planned orders:

```bash
PYTHONPATH=src python3 -m weather_bot.replay_report
```

Estimate paper portfolio mark-to-market PnL from latest recorded mids:

```bash
PYTHONPATH=src python3 -m weather_bot.reconcile
```

Basic health check on recent scan logs (source errors, stale data, zero-opportunity runs):

```bash
PYTHONPATH=src python3 -m weather_bot.health
```

Calibration report (match recorded predictions to settled weather buckets via Gamma event slugs):

```bash
WEATHER_BOT_UNIVERSE=tier1 \
WEATHER_BOT_CAL_DAYS_BACK=14 \
WEATHER_BOT_INSECURE_SSL=1 \
PYTHONPATH=src python3 -m weather_bot.calibration_report
```

Replay backtest (conservative fills/slippage on recorded scan snapshots):

```bash
WEATHER_BOT_SCAN_MODE=live_scan \
WEATHER_BOT_BT_SIZE_USD=5 \
WEATHER_BOT_BT_TP_PCT=0.35 \
WEATHER_BOT_BT_SL_PCT=0.20 \
WEATHER_BOT_BT_SLIPPAGE_BPS=50 \
PYTHONPATH=src python3 -m weather_bot.replay_backtest
```

Optional settlement against Gamma (if some recorded markets have since resolved):

```bash
WEATHER_BOT_SCAN_MODE=live_scan \
WEATHER_BOT_BT_SETTLE_WITH_GAMMA=1 \
WEATHER_BOT_UNIVERSE=tier1 \
WEATHER_BOT_CAL_DAYS_BACK=30 \
WEATHER_BOT_INSECURE_SSL=1 \
PYTHONPATH=src python3 -m weather_bot.replay_backtest
```

## Periodic Scan Runner (Calibration Data Collection)

Run repeated scans to accumulate pre-resolution predictions for later calibration:

```bash
WEATHER_BOT_MODE=live_scan \
WEATHER_BOT_UNIVERSE=tier1 \
WEATHER_BOT_RECORD_SCAN=1 \
WEATHER_BOT_INSECURE_SSL=1 \
WEATHER_BOT_RUNNER_INTERVAL_SECONDS=300 \
WEATHER_BOT_RUNNER_CYCLES=12 \
PYTHONPATH=src python3 -m weather_bot.runner
```

Artifacts:

- `data/sample/runner_cycles.jsonl`
- `data/sample/health_snapshots.jsonl`
- `data/sample/scan_snapshots.jsonl`

## Live Scan Universe Filter

`live_scan` supports a curated US weather universe selection:

- `WEATHER_BOT_UNIVERSE=tier1` (default): `NYC/KLGA`, `Atlanta/KATL`, `Dallas/KDAL`
- `WEATHER_BOT_UNIVERSE=tier2`: adds `Chicago/KORD`, `Seattle/KSEA`
- `WEATHER_BOT_UNIVERSE=all_us`: adds `Miami/KMIA`

## Suggested next steps

1. Implement a real market adapter (Polymarket CLOB snapshots / quotes).
2. Implement weather + observation adapters (NWS/NOAA, METAR).
3. Add exact resolution-source mapping for each weather market type.
4. Build replay/backtest using recorded snapshots.

## Testing

`pytest` tests are included, but `pytest` is not installed by default in this workspace.

If installed:

```bash
PYTHONPATH=src pytest -q
```

## Important

This scaffold is for research and validation. It does not place live trades.
