#!/bin/bash
# Nightly: refit split-conformal offsets on the live DB and reload x402.
# Runs after the 3 AM daily-pipeline DB refresh. Read-only on the DB.
set -e
DIR=/Users/thegreatluna8713/Documents/undesirables-x402-server
LIVE=/Users/thegreatluna8713/Documents/undesirables-mcp-server/.cache/market_memory.sqlite
cd "$DIR"
echo "[refresh_conformal] $(date) — refitting offsets"
python3 scripts/conformal_calibrate.py --db "$LIVE" --out "$DIR/conformal_offsets.json"
# x402 caches offsets at process start. It has KeepAlive=true, so killing the
# uvicorn worker triggers an immediate launchd respawn that reads the new file.
pkill -f "uvicorn server:app" || true
echo "[refresh_conformal] $(date) — reloaded x402"
