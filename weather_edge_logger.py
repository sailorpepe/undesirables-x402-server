#!/usr/bin/env python3
"""
weather_edge_logger.py — Snapshot the weather scanner's actionable edges as
timestamped PREDICTIONS, so they can later be resolved against actual outcomes
and turned into a real, verifiable hit-rate (the receipts).

Why this exists: the old edge tracker logged 0 resolutions in 13,973 records.
There was no way to prove the weather edges were legit. This is step 1 of fixing
that: capture every call we make, keyed by Kalshi market ticker.

Flow:
  hit localhost:3000/api/weather-edge  ->  for each actionable edge (signal != SKIP),
  upsert one record per ticker into ~/logs/weather_edges.jsonl (idempotent; updates
  the prediction each run until the market closes; never touches resolved records).

Resolution is done separately by weather_edge_resolver.py (ASOS actuals).
"""
import json
import os
import sys
from datetime import datetime, timezone

import requests

SHROOMY_URL = "http://127.0.0.1:3000/api/weather-edge"
LOG_PATH = os.path.expanduser("~/logs/weather_edges.jsonl")


def fetch_actionable():
    r = requests.get(SHROOMY_URL, timeout=45)
    r.raise_for_status()
    data = r.json()
    out = []
    for e in data.get("edges", []):
        if e.get("signal") in (None, "SKIP"):
            continue
        if e.get("edgeClass") in ("C", "D"):  # ghost trap / settled — not a forecast call
            continue
        m = e.get("market", {})
        out.append({
            "ticker": m.get("ticker"),
            "city": e.get("cityCode"),
            "market_type": m.get("marketType"),
            "strike_type": m.get("strikeType"),
            "strike_temp": m.get("strikeTemp"),
            "bracket_low": m.get("bracketLow"),
            "bracket_high": m.get("bracketHigh"),
            "target_date": m.get("targetDate"),
            "close_time": m.get("closeTime"),
            "signal": e.get("signal"),
            "our_prob": e.get("forecastProb"),
            "market_prob": e.get("marketYesProb"),
            "edge": round(e.get("edge", 0), 2),
            "cost_cents": e.get("costToBuy"),
            "edge_class": e.get("edgeClass"),
            "volume": m.get("volume"),
        })
    return out


def load_log():
    by_ticker = {}
    order = []
    if os.path.exists(LOG_PATH):
        with open(LOG_PATH) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                k = (rec.get("ticker"), rec.get("target_date"))
                if k not in by_ticker:
                    order.append(k)
                by_ticker[k] = rec
    return by_ticker, order


def main():
    now = datetime.now(timezone.utc).isoformat()
    try:
        edges = fetch_actionable()
    except Exception as e:
        print(f"[!] fetch failed: {e}")
        return 1

    by_ticker, order = load_log()
    new = 0
    for e in edges:
        if not e.get("ticker") or not e.get("target_date"):
            continue
        k = (e["ticker"], e["target_date"])
        existing = by_ticker.get(k)
        if existing is not None:
            # INSERT-ONLY: the first time we flag a market is our honest forward-looking
            # call. Don't overwrite it with later (near-locked) predictions — that would
            # inflate the hit-rate by scoring a call made once we already know the answer.
            continue
        e["first_logged_at"] = now
        e["resolved"] = False
        by_ticker[k] = e
        order.append(k)
        new += 1

    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    with open(LOG_PATH, "w") as f:
        for k in order:
            f.write(json.dumps(by_ticker[k]) + "\n")

    total = len(order)
    print(f"[{now}] {len(edges)} actionable edges seen → {new} new first-calls logged. "
          f"Log now holds {total} predictions ({sum(1 for k in order if by_ticker[k].get('resolved'))} resolved).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
