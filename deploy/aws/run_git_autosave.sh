#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="${ROOT_DIR:-/opt/weather-polymarket-bot}"
cd "$ROOT_DIR"

LOCK_FILE="${GIT_AUTOSAVE_LOCK_FILE:-$ROOT_DIR/.git/autosave.lock}"
mkdir -p "$(dirname "$LOCK_FILE")"

exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  echo "git autosave: lock busy, skipping"
  exit 0
fi

if [[ ! -d .git ]]; then
  echo "git autosave: not a git repo at $ROOT_DIR" >&2
  exit 1
fi

. .venv/bin/activate
set -a
if [[ -f ./.env.weather-bot ]]; then
  . ./.env.weather-bot
fi
set +a

REMOTE="${GIT_AUTOSAVE_REMOTE:-origin}"
BRANCH="${GIT_AUTOSAVE_BRANCH:-main}"
PUSH_ENABLED="${GIT_AUTOSAVE_PUSH:-1}"
AUTHOR_NAME="${GIT_AUTOSAVE_AUTHOR_NAME:-poly-weather-autosave}"
AUTHOR_EMAIL="${GIT_AUTOSAVE_AUTHOR_EMAIL:-autosave@local}"
MSG_PREFIX="${GIT_AUTOSAVE_MESSAGE_PREFIX:-autosave}"

if [[ -n "$(git status --porcelain)" ]]; then
  git add -A

  # Re-check after staging in case only ignored files changed.
  if [[ -z "$(git diff --cached --name-only)" ]]; then
    echo "git autosave: no staged changes after git add"
    exit 0
  fi

  TS_UTC="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  SUMMARY="$(git diff --cached --name-only | wc -l | tr -d ' ') files"
  git -c user.name="$AUTHOR_NAME" -c user.email="$AUTHOR_EMAIL" \
    commit -m "$MSG_PREFIX: $TS_UTC ($SUMMARY)"
  if [[ "$PUSH_ENABLED" == "1" || "$PUSH_ENABLED" == "true" || "$PUSH_ENABLED" == "yes" ]]; then
    git push "$REMOTE" "$BRANCH"
    echo "git autosave: pushed to $REMOTE/$BRANCH"
  else
    echo "git autosave: commit created (push disabled)"
  fi
else
  echo "git autosave: no changes"
fi

