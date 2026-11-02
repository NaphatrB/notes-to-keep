#!/bin/sh
# Apple Notes -> Google Keep importer (drip mode).
# Usage: ./run-import.sh [extra flags]   e.g. ./run-import.sh --dry-run
set -eu
cd "$(dirname "$0")"

PY="$HOME/.venvs/keep/bin/python"
EXPORT="$HOME/Documents/apple-notes-export"
CFG="$HOME/.config/keep"

if [ ! -x "$PY" ]; then
    echo "error: venv missing at $PY" >&2
    exit 1
fi

# Use the saved token/email from get-token.py unless the caller overrides.
if [ -z "${KEEP_MASTER_TOKEN:-}" ] && [ -f "$CFG/token" ]; then
    KEEP_MASTER_TOKEN=$(cat "$CFG/token")
    export KEEP_MASTER_TOKEN
fi
if [ -f "$CFG/email" ] && [ -z "${KEEP_EMAIL:-}" ]; then
    KEEP_EMAIL=$(cat "$CFG/email")
    export KEEP_EMAIL
fi

if [ -n "${KEEP_EMAIL:-}" ]; then
    set -- --email "$KEEP_EMAIL" "$@"
fi

# Batch 50 keeps request count low; the daily quota is roughly 200 notes, so a
# run does a few batches and stops when Google returns 429.
exec "$PY" import_notes_to_keep.py "$EXPORT" \
    --label-depth 1 \
    --keep-dates \
    --skip-empty \
    --batch-size 100 \
    --delay 0 \
    --timeout 180 \
    --sync-timeout 120 \
    "$@"
