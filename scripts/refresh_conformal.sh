#!/bin/bash
# Nightly: refit REGIME-AWARE split-conformal offsets on the live DB and reload
# x402. Emits per-vol-tercile (calm/medium/jumpy) bundles + a global fallback.
# Runs after the 3 AM daily-pipeline DB refresh. Read-only on the DB.
set -e
DIR=/Users/thegreatluna8713/Documents/undesirables-x402-server
LIVE=/Users/thegreatluna8713/Documents/undesirables-mcp-server/.cache/market_memory.sqlite
cd "$DIR"
echo "[refresh_conformal] $(date) — refitting offsets"
# --origins 5 = NexCP multi-origin recency-weighted fit (backtested 2026-07-14:
# calm VaR ~28% sharper OOT, safer VaR95 margins, 3x calibration data ->
# stabler night-to-night; deep-tail quantiles carry a multi-origin cushion).
# Retry once after 90s: 2026-07-16 the 04:00 run died instantly on a transient
# TCC denial of venv/pyvenv.cfg (same python ran fine at 05:20+). One retry
# outlives a tccd flake without masking a real persistent failure.
if ! "$DIR/venv/bin/python" scripts/conformal_calibrate.py --db "$LIVE" --out "$DIR/conformal_offsets.json" --origins 5; then
  echo "[refresh_conformal] first attempt failed — retrying in 90s"
  sleep 90
  "$DIR/venv/bin/python" scripts/conformal_calibrate.py --db "$LIVE" --out "$DIR/conformal_offsets.json" --origins 5
fi
# x402 caches offsets at process start. It has KeepAlive=true, so killing the
# uvicorn worker triggers an immediate launchd respawn that reads the new file.
pkill -f "uvicorn server:app" || true
echo "[refresh_conformal] $(date) — reloaded x402"
