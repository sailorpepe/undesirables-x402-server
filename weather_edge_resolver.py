#!/usr/bin/env python3
"""
weather_edge_resolver.py — Resolve logged weather predictions against ACTUAL
outcomes (ASOS observations, the same NWS data Kalshi settles on) and compute a
real, verifiable hit-rate + P&L. The receipts.

For each unresolved prediction past its close_time:
  - fetch the actual daily high/low for that city + date from the IEM ASOS archive
  - decide the market result (did the bracket / strike condition come true?)
  - score our call (BUY_YES wins if YES, BUY_NO wins if NO) and the realized P&L
  - mark the record resolved and rewrite the log

Then write ~/logs/weather_track_record.json (overall + by-signal + by-city + recent)
which daily_alpha.py can read to post the real track record instead of projections.

Resolution source = ASOS hourly max/min, rounded to integer °F (Kalshi settles on the
official NWS integer daily extreme; ASOS hourly is a close proxy, may differ ±1° on
rare days — flagged in each record as `resolution_source`).
"""
import json
import os
import sys
import csv
import io
import time
from datetime import datetime, timezone

import requests

LOG_PATH = os.path.expanduser("~/logs/weather_edges.jsonl")
TRACK_PATH = os.path.expanduser("~/logs/weather_track_record.json")

# city -> (IEM ASOS station id, IANA tz). Same stations the model observes.
CITY_STATION = {
    "NYC": ("JFK", "America/New_York"), "CHI": ("ORD", "America/Chicago"),
    "LAS": ("LAS", "America/Los_Angeles"), "MIA": ("MIA", "America/New_York"),
    "DEN": ("DEN", "America/Denver"), "AUS": ("AUS", "America/Chicago"),
    "HOU": ("IAH", "America/Chicago"), "PHX": ("PHX", "America/Phoenix"),
    "ATL": ("ATL", "America/New_York"), "PHI": ("PHL", "America/New_York"),
}

_asos_cache = {}


def fetch_actual_extreme(city, date_str, want_high, tries=4):
    """Return the rounded integer actual high (or low) for city on date_str (YYYY-MM-DD)."""
    if city not in CITY_STATION:
        return None
    stn, tz = CITY_STATION[city]
    ck = (stn, date_str)
    if ck not in _asos_cache:
        y, m, d = date_str.split("-")
        url = ("https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py"
               f"?station={stn}&data=tmpf&year1={y}&month1={int(m)}&day1={int(d)}"
               f"&year2={y}&month2={int(m)}&day2={int(d)}&tz={tz}"
               "&format=onlycomma&latlon=no&missing=M&trace=T&report_type=3")
        temps = None
        for _ in range(tries):
            try:
                r = requests.get(url, timeout=90)
                if r.status_code == 200 and len(r.text) > 100:
                    temps = []
                    for row in csv.reader(io.StringIO(r.text)):
                        if len(row) < 3 or row[0] == "station":
                            continue
                        v = row[2]
                        if v in ("M", "T", ""):
                            continue
                        try:
                            temps.append(float(v))
                        except ValueError:
                            pass
                    break
            except Exception:
                pass
            time.sleep(3)
        _asos_cache[ck] = temps
    temps = _asos_cache[ck]
    if not temps:
        return None
    return round(max(temps)) if want_high else round(min(temps))


def market_resolves_yes(rec, actual):
    """Did the market's YES condition come true, given the actual integer temp?"""
    st = rec.get("strike_type")
    if st == "bracket":
        lo, hi = rec.get("bracket_low"), rec.get("bracket_high")
        if lo is None or hi is None:
            return None
        return lo <= actual <= hi
    strike = rec.get("strike_temp")
    if strike is None:
        return None
    if st == "above":
        return actual >= strike
    if st == "below":
        return actual <= strike
    return None


def main():
    if not os.path.exists(LOG_PATH):
        print("No log yet.")
        return 0
    recs = []
    with open(LOG_PATH) as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    recs.append(json.loads(line))
                except json.JSONDecodeError:
                    pass

    now = datetime.now(timezone.utc)
    newly = 0
    for rec in recs:
        if rec.get("resolved"):
            continue
        ct = rec.get("close_time")
        if not ct:
            continue
        try:
            close = datetime.fromisoformat(ct.replace("Z", "+00:00"))
        except ValueError:
            continue
        if now <= close:
            continue  # market not closed yet

        want_high = rec.get("market_type") == "HIGH"
        actual = fetch_actual_extreme(rec.get("city"), rec.get("target_date"), want_high)
        if actual is None:
            continue  # ASOS unavailable; try again next run
        yes = market_resolves_yes(rec, actual)
        if yes is None:
            continue
        result = "YES" if yes else "NO"
        sig = rec.get("signal")
        won = (sig == "BUY_YES" and yes) or (sig == "BUY_NO" and not yes)
        cost = rec.get("cost_cents") or 0
        pnl = (100 - cost) if won else -cost  # cents per 1-contract stake

        rec["resolved"] = True
        rec["resolved_at"] = now.isoformat()
        rec["actual_temp"] = actual
        rec["market_result"] = result
        rec["won"] = won
        rec["pnl_cents"] = pnl
        rec["resolution_source"] = "ASOS hourly (rounded)"
        newly += 1

    with open(LOG_PATH, "w") as f:
        for rec in recs:
            f.write(json.dumps(rec) + "\n")

    # ---- aggregate track record ----
    resolved = [r for r in recs if r.get("resolved")]
    def agg(rows):
        n = len(rows)
        w = sum(1 for r in rows if r.get("won"))
        pnl = sum(r.get("pnl_cents", 0) for r in rows)
        cost = sum((r.get("cost_cents") or 0) for r in rows)
        return {
            "n": n, "wins": w, "losses": n - w,
            "hit_rate": round(100 * w / n, 1) if n else None,
            "pnl_cents": pnl,
            "roi_pct": round(100 * pnl / cost, 1) if cost else None,
        }

    by_signal, by_city = {}, {}
    for s in ("BUY_YES", "BUY_NO"):
        rows = [r for r in resolved if r.get("signal") == s]
        if rows:
            by_signal[s] = agg(rows)
    for c in sorted({r.get("city") for r in resolved if r.get("city")}):
        by_city[c] = agg([r for r in resolved if r.get("city") == c])

    track = {
        "updated_at": now.isoformat(),
        "overall": agg(resolved),
        "by_signal": by_signal,
        "by_city": by_city,
        "recent": [
            {k: r.get(k) for k in ("ticker", "city", "signal", "our_prob",
                                   "market_prob", "actual_temp", "market_result", "won", "pnl_cents")}
            for r in sorted(resolved, key=lambda r: r.get("resolved_at", ""), reverse=True)[:15]
        ],
    }
    with open(TRACK_PATH, "w") as f:
        json.dump(track, f, indent=2)

    o = track["overall"]
    print(f"Resolved {newly} new. Track record: {o['wins']}/{o['n']} "
          f"({o['hit_rate']}%), P&L {o['pnl_cents']}¢, ROI {o['roi_pct']}%. → {TRACK_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
