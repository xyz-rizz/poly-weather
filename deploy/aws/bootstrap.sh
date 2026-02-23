#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"

python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip

# Project currently uses stdlib only; install editable package for stable module execution.
pip install -e .

mkdir -p data/sample

if [[ ! -f .env.weather-bot ]]; then
  cp deploy/aws/weather-bot.env.example .env.weather-bot
  echo "Created .env.weather-bot from template. Edit it before starting services."
fi

echo "Bootstrap complete."
echo "Next: edit .env.weather-bot and install systemd units from deploy/systemd/."

