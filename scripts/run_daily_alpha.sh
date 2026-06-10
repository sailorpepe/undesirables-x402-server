#!/bin/bash
# ═══════════════════════════════════════════════════
# Daily Alpha Tweet — Wrapper for launchd
# Sources X_ env vars from .env and runs daily_alpha.py
# ═══════════════════════════════════════════════════
set -euo pipefail

cd /Users/thegreatluna8713/Documents/undesirables-x402-server

# Load X_ env vars from .env
if [ -f .env ]; then
    export $(grep "^X_" .env | xargs)
else
    echo "[ERROR] .env not found" >&2
    exit 1
fi

# Run the daily alpha bot
exec ./venv/bin/python3 daily_alpha.py
