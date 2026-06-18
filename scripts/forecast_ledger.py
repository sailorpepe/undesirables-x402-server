#!/usr/bin/env python3
"""
forecast_ledger.py — nightly APPEND-ONLY snapshot of what the live oracle would
return (model=conformal) for a liquidity-ranked card universe, so each forecast
locks a forward-only TRACK RECORD that can later be scored against realized price.

Mirrors the served endpoint EXACTLY:
  - mu, sigma = _get_calibrated_params equivalent (served_params on the card's
    FULL all-sub_type history — the endpoint applies NO sub_type filter there).
  - point = current_price * exp(mu * h / 365)
  - regime by sigma vs regime_thresholds; bands + VaR from that regime's bundle
    in conformal_offsets.json (global fallback if no regime / sigma missing).
If a ledger row diverges from /api/v1/simulate?model=conformal, it's wrong.

Liquidity proxy (best available — no volume field exists, spread is polluted by
troll high_prices): continuity x dollar-weight = (priced_dates/total)*log1p(price),
on Normal singles with >=70 priced days in 90d and price >= $2.

READ-ONLY on market_memory.sqlite (mode=ro). Writes a SEPARATE forecast_ledger.sqlite.
Append-only/idempotent: one row per (forecast_date, product_id, sub_type, horizon).
numpy only (no `random`); deterministic. Additive — touches no serving code.
"""
import os, sys, math, json, sqlite3, argparse
from datetime import datetime, date
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import conformal_calibrate as C          # reuse the verified served_params

HORIZONS = [7, 14, 30]
SUBTYPE = "Normal"
HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
DEF_MARKET = os.path.join(REPO, "..", "undesirables-mcp-server", ".cache", "market_memory.sqlite")
DEF_LEDGER = os.path.join(REPO, "forecast_ledger.sqlite")
DEF_OFFSETS = os.path.join(REPO, "conformal_offsets.json")


def ensure_schema(led):
    led.execute("""CREATE TABLE IF NOT EXISTS forecast_universe (
        forecast_date TEXT, product_id INTEGER, sub_type TEXT, rank INTEGER,
        proxy_score REAL, publish_flag INTEGER,
        PRIMARY KEY (forecast_date, product_id, sub_type))""")
    led.execute("""CREATE TABLE IF NOT EXISTS forecast_ledger (
        forecast_date TEXT, product_id INTEGER, card_name TEXT, sub_type TEXT, horizon INTEGER,
        current_price REAL, point REAL, band_50_low REAL, band_50_high REAL,
        band_90_low REAL, band_90_high REAL, var95_pct REAL, var99_pct REAL,
        regime TEXT, mu_annual REAL, sigma_annual REAL, prob_up REAL, drift_spike INTEGER,
        offsets_fit_date TEXT, created_at TEXT,
        PRIMARY KEY (forecast_date, product_id, sub_type, horizon))""")
    try:                                   # add drift_spike to pre-existing ledgers
        led.execute("ALTER TABLE forecast_ledger ADD COLUMN drift_spike INTEGER")
    except Exception:
        pass
    led.commit()


def rank_universe(mkt, as_of, universe_top):
    """Composite liquidity proxy: continuity x log1p(price). Returns ranked list of
    (product_id, name, latest_price, proxy_score)."""
    total_dates = mkt.execute("SELECT COUNT(DISTINCT date) FROM price_history "
                              "WHERE date>=date(?, '-90 day')", [as_of]).fetchone()[0]
    rows = mkt.execute("""
        WITH cont AS (SELECT product_id, COUNT(DISTINCT date) d FROM price_history
                      WHERE sub_type=? AND market_price>0 AND date>=date(?, '-90 day') GROUP BY product_id),
             latest AS (SELECT product_id, market_price lp FROM price_history
                        WHERE sub_type=? AND date=? AND market_price>0),
             mm AS (SELECT product_id, MIN(market_price) mn, MAX(market_price) mx FROM price_history
                    WHERE sub_type=? AND market_price>0 GROUP BY product_id)
        SELECT cont.product_id, cont.d, latest.lp
        FROM cont JOIN latest USING(product_id) JOIN mm USING(product_id)
        WHERE cont.d>=70 AND latest.lp>=2 AND mm.mx>mm.mn
    """, [SUBTYPE, as_of, SUBTYPE, as_of, SUBTYPE]).fetchall()
    scored = []
    for pid, d, lp in rows:
        score = (d / total_dates) * math.log1p(lp)
        scored.append((pid, lp, score))
    scored.sort(key=lambda r: (-r[2], -r[1], r[0]))     # deterministic tie-break
    scored = scored[:universe_top]
    names = dict(mkt.execute("SELECT product_id, name FROM cards").fetchall())
    return [(pid, names.get(pid, str(pid)), lp, sc) for pid, lp, sc in scored]


def select_bundle(off, sigma):
    """EXACT replica of server.py _conformal_forecast bundle/regime selection."""
    bundle, regime = None, None
    regs = off.get("regimes")
    th = (off.get("regime_thresholds") or {}).get("sigma_annual")
    if regs and sigma is not None and th and len(th) == 2:
        regime = "calm" if sigma <= th[0] else ("medium" if sigma <= th[1] else "jumpy")
        bundle = regs.get(regime)
    if bundle is None and "bands" in off and "var95" in off:
        bundle = {"bands": off["bands"], "var95": off["var95"], "var99": off.get("var99", off["var95"])}
        regime = "global"
    return bundle, regime


def full_history(mkt, pid):
    """All-sub_type history (mirrors _get_calibrated_params — NO sub_type filter)."""
    rows = mkt.execute("SELECT date, market_price FROM price_history WHERE product_id=? "
                       "AND market_price IS NOT NULL AND market_price>0 ORDER BY date ASC", [pid]).fetchall()
    dates = [datetime.strptime(r[0], "%Y-%m-%d").date() for r in rows]
    prices = [float(r[1]) for r in rows]
    return dates, prices


def prob_up(point, off50, off90, S0):
    """P(price_h > S0) via piecewise-linear CDF through the published band percentiles."""
    xs = np.array([max(0.0, point - off90), max(0.0, point - off50), point, point + off50, point + off90])
    ys = np.array([0.05, 0.25, 0.50, 0.75, 0.95])
    order = np.argsort(xs)                               # guard monotonicity
    cdf = float(np.interp(S0, xs[order], ys[order]))
    return round(float(np.clip(1.0 - cdf, 0.0, 1.0)), 4)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--market-db", default=DEF_MARKET)
    ap.add_argument("--ledger-db", default=DEF_LEDGER)
    ap.add_argument("--offsets", default=DEF_OFFSETS)
    ap.add_argument("--publish-top", type=int, default=200)
    ap.add_argument("--universe-top", type=int, default=2000)
    a = ap.parse_args()

    mkt = sqlite3.connect(f"file:{os.path.abspath(a.market_db)}?mode=ro", uri=True)
    led = sqlite3.connect(a.ledger_db)
    ensure_schema(led)
    off = json.load(open(a.offsets))
    max_h = int(off.get("max_horizon", 0)); fit_date = off.get("fit_date")

    as_of = mkt.execute("SELECT MAX(date) FROM price_history").fetchone()[0]
    now = datetime.now().isoformat(timespec="seconds")
    print(f"[ledger] as-of {as_of} | offsets fit_date {fit_date} | horizons {HORIZONS}", flush=True)

    universe = rank_universe(mkt, as_of, a.universe_top)
    # write universe (append-only, idempotent)
    u_rows = [(as_of, pid, SUBTYPE, i + 1, round(sc, 6), 1 if i < a.publish_top else 0)
              for i, (pid, name, lp, sc) in enumerate(universe)]
    led.executemany("INSERT OR IGNORE INTO forecast_universe VALUES (?,?,?,?,?,?)", u_rows)
    led.commit()
    pub = sum(1 for r in u_rows if r[5] == 1)
    print(f"[universe] {len(u_rows)} ranked ({pub} publish / {len(u_rows)-pub} private)", flush=True)

    written = skipped = 0
    for pid, name, lp, sc in universe:
        dates, prices = full_history(mkt, pid)
        if len(prices) < 5:
            continue
        mu, sigma, spike = C.served_params(dates, prices)
        if mu is None:
            continue
        # S0 = latest price for THIS (product, sub_type)
        s0row = mkt.execute("SELECT market_price FROM price_history WHERE product_id=? AND sub_type=? "
                            "AND date=? AND market_price>0", [pid, SUBTYPE, as_of]).fetchone()
        if not s0row:
            continue
        S0 = float(s0row[0])
        bundle, regime = select_bundle(off, sigma)
        if bundle is None:
            continue
        for h in HORIZONS:
            if h > max_h:
                continue
            idx = min(h, len(bundle["bands"]["0.50"])) - 1
            point = S0 * math.exp(mu * h / 365.0)
            off50 = bundle["bands"]["0.50"][idx] * S0
            off90 = bundle["bands"]["0.90"][idx] * S0
            var95 = max(0.0, point - bundle["var95"][idx] * S0)
            var99 = max(0.0, point - bundle["var99"][idx] * S0)
            row = (as_of, pid, name, SUBTYPE, h, round(S0, 4), round(point, 4),
                   round(max(0.0, point - off50), 4), round(point + off50, 4),
                   round(max(0.0, point - off90), 4), round(point + off90, 4),
                   round((var95 - S0) / S0 * 100, 2), round((var99 - S0) / S0 * 100, 2),
                   regime, mu, sigma, prob_up(point, off50, off90, S0), int(spike), fit_date, now)
            cur = led.execute("INSERT OR IGNORE INTO forecast_ledger VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", row)
            if cur.rowcount: written += 1
            else: skipped += 1
    led.commit()
    print(f"[ledger] rows written {written} | skipped (already locked) {skipped}", flush=True)
    mkt.close(); led.close()


if __name__ == "__main__":
    main()
