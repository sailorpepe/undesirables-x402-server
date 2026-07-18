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
#
# RETRIES (forensics 2026-07-16): the 04:00 spawn hit a transient macOS TCC
# denial (EPERM on venv/pyvenv.cfg at interpreter startup) on Jul 2/4/6/8/12/16
# — ~35% of nights, ALWAYS at 04:00:0x, colliding with the graded-enrichment
# launchd spawn at the same second. Cron entry moved to 04:03 to de-collide;
# these retries (90s, 300s) outlive any residual tccd flap; on total failure
# we ntfy IMMEDIATELY (the 07:00 healthcheck fit_date check is the backstop).
fit() { "$DIR/venv/bin/python" scripts/conformal_calibrate.py --db "$LIVE" --out "$DIR/conformal_offsets.json" --origins 5; }
if ! fit; then
  echo "[refresh_conformal] attempt 1 failed — retrying in 90s"
  sleep 90
  if ! fit; then
    echo "[refresh_conformal] attempt 2 failed — retrying in 300s"
    sleep 300
    if ! fit; then
      echo "[refresh_conformal] ALL attempts failed"
      # TCC-safe topic lookup: on 2026-07-17 the denial window covered .env
      # too, so the failure alert itself was denied. ~/.config is outside
      # ~/Documents and always readable.
      TOPIC=$(cat "$HOME/.config/undesirables_ntfy_topic" 2>/dev/null || grep '^NTFY_TOPIC=' "$DIR/.env" | cut -d= -f2)
      [ -n "$TOPIC" ] && curl -s -m 15 -X POST "https://ntfy.sh/$TOPIC" \
        -H "Title: Conformal refit FAILED (3 attempts)" -H "Priority: high" -H "Tags: rotating_light" \
        -d "All 3 refit attempts failed at $(date). Server keeps yesterday's offsets. Check ~/logs/conformal_refresh.log — recurring TCC EPERM pattern documented in MASTER_TRACKER Jul-16." >/dev/null
      exit 1
    fi
  fi
fi
# x402 caches offsets at process start. It has KeepAlive=true, so killing the
# uvicorn worker triggers an immediate launchd respawn that reads the new file.
pkill -f "uvicorn server:app" || true
echo "[refresh_conformal] $(date) — reloaded x402"
