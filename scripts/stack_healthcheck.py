#!/usr/bin/env python3
"""
stack_healthcheck.py — the stack-wide dead-man's switch. One daily pass over
EVERY data stream + service with an expected cadence; anything stale, missing,
or anomalous fires an ntfy phone alert (high priority). Silence = green.

WHY: silent failures are this stack's #1 failure mode — proven three times:
  - vibes_dyli_ingest broken 3 DAYS (Jul 5-8, schema bug, 2 days of data lost)
  - vibes_ebay_tracker crashed on the same bug class (caught day 2)
  - preimage backup pushed nowhere for 3 nights (no headless creds)
Each was found by a lucky manual audit. This makes the finding automatic.

Checks (freshness + sanity):
  market prices, Vibes DYLI (+row-count cliff), Vibes eBay, graded, sales tape,
  pulls, forecast ledger, conformal offsets, forecast feed, soul predictions
  (Mondays: new week locked + root committed), preimage backup off-machine
  confirmation, oracle HTTP, Glitch heartbeat, hourly on-chain pusher logs.

Cron: 07:00 daily (after the whole morning choreography). Zero API cost —
reads local stores + two localhost/tunnel HTTP checks.
"""
import os, json, sqlite3, urllib.request
from datetime import datetime, date, timedelta

H = os.path.expanduser
MKT = H("~/Documents/undesirables-mcp-server/.cache/market_memory.sqlite")
SAL = H("~/Documents/undesirables-mcp-server/.cache/dyli_sales.sqlite")
X = H("~/Documents/undesirables-x402-server")
LOGS = H("~/logs")
problems, notes = [], []


def ro(p):
    return sqlite3.connect(f"file:{p}?mode=ro", uri=True)


def check(name, ok, detail=""):
    (notes if ok else problems).append(f"{'✓' if ok else '✗'} {name}" + (f" — {detail}" if detail else ""))


def mtime_within(path, hours):
    try:
        return (datetime.now() - datetime.fromtimestamp(os.path.getmtime(path))) < timedelta(hours=hours)
    except OSError:
        return False


def main():
    today = date.today().isoformat()
    yday = (date.today() - timedelta(days=1)).isoformat()

    # ── market data ──
    m = ro(MKT)
    d = m.execute("SELECT MAX(date) FROM price_history WHERE product_id<9500000").fetchone()[0]
    check("TCGCSV prices", d >= yday, f"max {d} (expect ≥ {yday}: structural 1-day lag)")

    d = m.execute("SELECT MAX(date) FROM vibes_price_history WHERE source='dyli'").fetchone()[0]
    check("Vibes DYLI", d == today, f"max {d}")
    if d == today:   # row-count cliff detection (a half-broken ingest writes few rows)
        n_t = m.execute("SELECT COUNT(*) FROM vibes_price_history WHERE source='dyli' AND date=?", (today,)).fetchone()[0]
        n_y = m.execute("SELECT COUNT(*) FROM vibes_price_history WHERE source='dyli' AND date=?", (yday,)).fetchone()[0]
        check("Vibes DYLI row-count", n_y == 0 or n_t > 0.7 * n_y, f"{n_t} today vs {n_y} yesterday")

    d = m.execute("SELECT MAX(date) FROM vibes_ebay_history").fetchone()[0]
    check("Vibes eBay", d == today, f"max {d}")
    d = m.execute("SELECT MAX(fetched_at) FROM graded_prices").fetchone()[0] or ""
    check("graded_prices", d[:10] >= yday, f"last fetch {d[:16]}")

    s = ro(SAL)
    d = s.execute("SELECT MAX(captured_at) FROM dyli_sales_events").fetchone()[0] or ""
    check("sales tape (15-min poller)", d >= (datetime.utcnow() - timedelta(hours=2)).isoformat()[:16],
          f"last capture {d[:16]}Z")

    # ── forecast layer ──
    l = ro(os.path.join(X, "forecast_ledger.sqlite"))
    d = l.execute("SELECT MAX(forecast_date) FROM forecast_ledger").fetchone()[0]
    check("forecast ledger", d >= yday, f"max {d}")
    try:
        fit = json.load(open(os.path.join(X, "conformal_offsets.json"))).get("fit_date