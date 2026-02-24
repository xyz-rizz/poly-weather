# AWS / VPS Deployment (Paper Mode First)

This project is ready to run on a VPS so you can collect data continuously without keeping your laptop on.

Recommended rollout order:

1. `paper-mode runner` (continuous scans + logs)
2. `settlement-triggered calibration refresh` (checks for new settled overlaps and refreshes when needed)
3. `daily calibration refresh` fallback (optional)
4. `git autosave` (optional, every 30 min)
5. `monitoring/alerts`
6. `shadow live execution` (intent logging only, no real orders)
7. only later: real live execution

## Recommended Instance (AWS)

- `EC2` in `us-east-1` (simple and flexible)
- Ubuntu 24.04 LTS
- small instance is enough for current workload (scanner/calibration are light)

You can also use Lightsail / Hetzner / DigitalOcean. The setup below is generic Linux + systemd.

## One-Time Server Setup

SSH in and run:

```bash
sudo apt update
sudo apt install -y git python3 python3-venv python3-pip tmux
```

Clone repo and bootstrap:

```bash
git clone <your-repo-url> /opt/weather-polymarket-bot
cd /opt/weather-polymarket-bot
bash deploy/aws/bootstrap.sh
```

## Configure Environment

Copy and edit:

```bash
cp deploy/aws/weather-bot.env.example .env.weather-bot
nano .env.weather-bot
```

Minimum required:

- `WEATHER_BOT_USER_AGENT` (use a real contact email)
- `WEATHER_BOT_INSECURE_SSL` only if your VPS has CA issues (prefer `0`)

## Install Services

```bash
sudo cp deploy/systemd/weather-bot-runner.service /etc/systemd/system/
sudo cp deploy/systemd/weather-bot-calibration-refresh.service /etc/systemd/system/
sudo cp deploy/systemd/weather-bot-calibration-refresh.timer /etc/systemd/system/
sudo cp deploy/systemd/weather-bot-settlement-trigger.service /etc/systemd/system/
sudo cp deploy/systemd/weather-bot-settlement-trigger.timer /etc/systemd/system/
sudo cp deploy/systemd/weather-bot-paper-settlement-reconcile.service /etc/systemd/system/
sudo cp deploy/systemd/weather-bot-git-autosave.service /etc/systemd/system/
sudo cp deploy/systemd/weather-bot-git-autosave.timer /etc/systemd/system/
sudo systemctl daemon-reload
```

Update service env path if needed (default assumes repo at `/opt/weather-polymarket-bot`).

## Start Runner (Continuous)

```bash
sudo systemctl enable --now weather-bot-runner.service
sudo systemctl status weather-bot-runner.service
```

Logs:

```bash
journalctl -u weather-bot-runner.service -f
```

## Start Daily Calibration Refresh

Recommended primary mode:

```bash
sudo systemctl enable --now weather-bot-settlement-trigger.timer
sudo systemctl list-timers | rg weather-bot-settlement
```

This checks every ~15 minutes and runs calibration refresh only when new settled markets overlap your stored scans.
It also runs the paper settlement reconciler on every check so paper positions can close on settlement or mark-based TP/SL/time-stop triggers.

Calibration/benchmark integrity guards (recommended defaults in `.env.weather-bot`):
- `WEATHER_BOT_FEATURE_LABEL_MIN_HOURS_TO_END=0` (prevents post-target label leakage in feature export)
- `WEATHER_BOT_BT_DEDUPE_MARKET=1` (threshold sweep counts one decision per market)
- `WEATHER_BOT_BT_MAX_POSITIONS_PER_EVENT=1` (prevents overcounting mutually exclusive buckets in one daily event)
- `WEATHER_BOT_BT_MIN_HOURS_TO_END=0` (threshold sweep ignores post-target rows defensively)

Paper mark-exit tuning (optional in `.env.weather-bot`):
- `WEATHER_BOT_PAPER_MARK_EXITS_ENABLED` (default `1`)
- `WEATHER_BOT_PAPER_MARK_MAX_AGE_SECONDS` (default `1800`)
- `WEATHER_BOT_PAPER_TAKE_PROFIT_PCT` (default `0.35`)
- `WEATHER_BOT_PAPER_STOP_LOSS_PCT` (default `0.20`)
- `WEATHER_BOT_PAPER_TIME_STOP_GRACE_SECONDS` (default `300`)
- `WEATHER_BOT_PAPER_PARTIAL_TP_ENABLED` (default `1`)
- `WEATHER_BOT_PAPER_PARTIAL_TP_FRACTION` (default `0.5`)
- `WEATHER_BOT_PAPER_PARTIAL_TP_MIN_CLOSE_USD` (default `1.0`)
- `WEATHER_BOT_PAPER_PARTIAL_TP_MIN_REMAINING_USD` (default `1.0`)
- `WEATHER_BOT_PAPER_CORE_BREAK_EVEN_ENABLED` (default `1`)
- `WEATHER_BOT_PAPER_CORE_BREAK_EVEN_BUFFER_PCT` (default `0.02`)
- `WEATHER_BOT_PAPER_CORE_TRAILING_ENABLED` (default `1`)
- `WEATHER_BOT_PAPER_CORE_TRAILING_DRAWDOWN_PCT` (default `0.15`)
- `WEATHER_BOT_PAPER_CORE_TRAILING_MIN_PEAK_RETURN_PCT` (default `0.25`)
- `WEATHER_BOT_PAPER_MIN_HOLD_MINUTES_BEFORE_SL` (default `20`)
- `WEATHER_BOT_PAPER_MAX_SPREAD_FOR_SL` (default `0.12`)
- `WEATHER_BOT_PAPER_MAX_SPREAD_FOR_TP` (default `0.18`)
- `WEATHER_BOT_PAPER_EXIT_REGIME_PROFILE_PATH` (JSON file for city/horizon overrides)
- `WEATHER_BOT_PAPER_EXIT_REGIME_PROFILE_JSON` (inline JSON overrides; useful for quick experiments)

Fallback daily mode (optional, can run alongside trigger):

```bash
sudo systemctl enable --now weather-bot-calibration-refresh.timer
sudo systemctl list-timers | rg weather-bot
```

Manual run:

```bash
sudo systemctl start weather-bot-calibration-refresh.service
journalctl -u weather-bot-calibration-refresh.service -n 200 --no-pager
```

Manual trigger check:

```bash
sudo systemctl start weather-bot-settlement-trigger.service
journalctl -u weather-bot-settlement-trigger.service -n 200 --no-pager
```

Manual paper settlement reconcile (independent):

```bash
sudo systemctl start weather-bot-paper-settlement-reconcile.service
journalctl -u weather-bot-paper-settlement-reconcile.service -n 200 --no-pager
```

## Optional: Git Autosave Every 30 Minutes

This will commit and push runtime changes (scan snapshots, reports, journals) to GitHub automatically.

```bash
sudo systemctl enable --now weather-bot-git-autosave.timer
sudo systemctl list-timers | rg weather-bot-git-autosave
```

Manual run:

```bash
sudo systemctl start weather-bot-git-autosave.service
journalctl -u weather-bot-git-autosave.service -n 200 --no-pager
```

Notes:
- It commits only when there are actual git changes.
- It uses a file lock to avoid overlapping runs.
- This will grow the repository quickly because `scan_snapshots.jsonl` is large and changes frequently.

## Data / Outputs to Watch

- `data/sample/scan_snapshots.jsonl`
- `data/sample/feature_rows_export.jsonl`
- `data/sample/calibration_profile.json`
- `data/sample/calibration_effectiveness_report.json`
- `data/sample/calibration_walkforward_report.json`
- `data/sample/threshold_sweep_report.json`
- `data/sample/settlement_trigger_state.json`
- `data/sample/paper_settlement_state.json`
- `data/sample/paper_settlement_ledger.jsonl`
- `data/sample/paper_settlement_report.json`
- `data/sample/paper_performance_report.json`

`paper_performance_report.json` now includes breakdowns by `exit_reason`, `city`, `direction`, `entry_horizon`, `exit_regime`, and `partial-vs-full` exits. With partial TP enabled, the remaining tranche can be protected by break-even/trailing-stop logic before settlement, and mark exits can be filtered by hold-time/spread quality guards.

## Execution Modes (Guarded)

The project now includes guarded execution modes:
- `shadow_submit` (intent logging only)
- `dry_run` (builds CLOB payloads and logs synthetic submit results, no real orders)
- `live_canary` (attempts real CLOB submits; use only with tiny caps and credentials)

The scaffold:
- builds live order intents from accepted opportunities
- enforces kill-switch guards (paper performance, stale scan age, exposure)
- logs intended orders to `data/sample/live_execution_attempts.jsonl`
- logs submit results to `data/sample/live_execution_results.jsonl` for `dry_run` / `live_canary`

Enable in `.env.weather-bot`:

```bash
WEATHER_BOT_EXECUTION_MODE=shadow_submit
WEATHER_BOT_EXEC_ALLOW=1
```

Keep `WEATHER_BOT_EXEC_ALLOW=0` unless you intentionally want the VPS to produce shadow execution intent logs.

Recommended progression:
1. `shadow_submit` with `WEATHER_BOT_EXEC_ALLOW=1`
2. `dry_run` (validate token IDs/payloads and guard behavior)
3. `live_canary` with:
   - `WEATHER_BOT_EXEC_CANARY_MAX_ORDER_USD=5`
   - `WEATHER_BOT_EXEC_CANARY_MAX_NOTIONAL_PER_SCAN_USD=5-10`
   - `WEATHER_BOT_EXEC_MAX_SUBMITS_PER_SCAN=1`
   - `POLYMARKET_PRIVATE_KEY` configured
   - optional `py-clob-client` installed in the VPS venv

## Security Notes (Important)

- Keep this in `paper mode` on the VPS for now.
- Do not store exchange private keys on the server yet.
- Use a non-root user for normal operations.
- Restrict SSH (`ufw` / security group).
- Keep the instance clock synced (default `systemd-timesyncd` is usually enough).
