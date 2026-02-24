# AWS / VPS Deployment (Paper Mode First)

This project is ready to run on a VPS so you can collect data continuously without keeping your laptop on.

Recommended rollout order:

1. `paper-mode runner` (continuous scans + logs)
2. `settlement-triggered calibration refresh` (checks for new settled overlaps and refreshes when needed)
3. `daily calibration refresh` fallback (optional)
3. `monitoring/alerts`
4. only later: live execution (not included yet)

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

## Data / Outputs to Watch

- `data/sample/scan_snapshots.jsonl`
- `data/sample/feature_rows_export.jsonl`
- `data/sample/calibration_profile.json`
- `data/sample/calibration_effectiveness_report.json`
- `data/sample/calibration_walkforward_report.json`
- `data/sample/threshold_sweep_report.json`
- `data/sample/settlement_trigger_state.json`

## Security Notes (Important)

- Keep this in `paper mode` on the VPS for now.
- Do not store exchange private keys on the server yet.
- Use a non-root user for normal operations.
- Restrict SSH (`ufw` / security group).
- Keep the instance clock synced (default `systemd-timesyncd` is usually enough).
