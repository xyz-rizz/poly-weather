#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="${ROOT_DIR:-/opt/weather-polymarket-bot}"
cd "$ROOT_DIR"

. .venv/bin/activate
set -a
. ./.env.weather-bot
set +a

exec python -m weather_bot.paper_settlement_reconcile

