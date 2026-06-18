#!/usr/bin/env python3
"""
conformal_calibrate.py — fit REGIME-AWARE split-conformal offsets for
/api/v1/simulate?model=conformal and write conformal_offsets.json.

Calm cards get tight bands, jumpy cards wide ones — honest AND discriminating,
instead of one blanket global band. The serving endpoint (server.py) buckets a
card by cal['sigma_annual'] from _get_calibrated_params against
regime_thresholds.sigma_annual, then uses that regime's offset arrays; it falls
back to the top-level global arrays if the file has no regimes or sigma is
unavailable. So the output stays backward-compatible.

Point forecast matches the endpoint EXACTLY:
    mu, sigma = v3 calibrator (ISO-week gap-scaled sigma + CAGR + Ito), rounded 4dp
    point_h   = current_price * exp(mu * h / 365)
Both mu and sigma are computed on the information set at forecast time (the
CONTEXT) — identical to serve, where the context is the card's full history.

Offsets are fractions of current_price, per step h=1..30:
    bands[L] = quantile_L(|actual - point| / price)        (symmetric half-width)
    varP     = quantile_P((point - actual) / price)         (one-sided lower tail)

Read-only DB (mode=ro). Deterministic pick. Disjoint calib/eval. OOS-validated
per regime before writing.

Usage: python3 scripts/conformal_calibrate.py [--db PATH] [--out PATH] [--n 1500]
"""
import os, sys, math, sqlite3, json, argparse, statistics
from datetime import datetime, date

H = 30
SUBTYPE = "Normal"
BANDS_STORE = ["0.50", "0.90"]
BANDS_ALL = {"0.50": 0.50, "0.80": 0.80, "0.90": 0.90, "0.95": 0.95}
REGIMES = ["calm", "medium", "jumpy"]
HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_OUT = os.path.join(os.path.dirname(HERE), "conformal_offsets.json")


def served_params(dates, prices):
    """EXACT replica of server.py _get_calibrated_params main path -> (mu_annual,
    sigma_annual), both round(.,4). The endpoint buckets regime on sigma_annual."""
    span = (dates[-1] - dates[0]).days
    years = max(span / 365.0, 0.01)
    weekly = []
    if span >= 28:
        buckets = {}
        for d, p in zip(dates, prices):
            buckets[d.isocalendar()[:2]] = (d, p)
        sw = sorted(buckets)
        for i in range(1, len(sw)):
            pdt, pp = buckets[sw[i - 1]]; cdt, cp = buckets[sw[i]]
            if pp > 0 and cp > 0:
                wg = max((cdt - pdt).days / 7.0, 0.1)
                weekly.append(math.log(cp / pp) / math.sqrt(wg))
    daily = []
    for i in range(1, len(prices)):
        dd = (dates[i] - dates[i - 1]).days
        if dd <= 0:
            continue
        daily.append(math.log(prices[i] / prices[i - 1]) / math.sqrt(dd))
    if len(weekly) >= 8:
        sigma = statistics.stdev(weekly) * math.sqrt(52)
    elif len(daily) >= 5:
        sigma = statistics.stdev(daily) * math.sqrt(365)
    else:
        return None, None
    cagr = math.log(prices[-1] / prices[0]) / years
    mu = cagr + 0.5 * sigma ** 2
    return round(mu, 4), round(sigma, 4)


def conf_q(scores, cov):
    s = sorted(scores); n = len(s)
    lvl = min(1.0, math.ceil((n + 1) * cov) / n)
    if lvl <= 0: return s[0]
    if lvl >= 1: return s[-1]
    return s[min(len(s) - 1, math.ceil(lvl * (len(s) - 1)))]


def load(db, n):
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    nd = con.execute("SELECT COUNT(DISTINCT date) FROM price_history").fetchone()[0]
    pids = con.execute(f"""WITH s AS (
        SELECT product_id, COUNT(*) c, MIN(market_price) mn, MAX(market_price) mx, AVG(market_price) av,
               MAX(CASE WHEN date=(SELECT MAX(date) FROM price_history) THEN market_price END) lp
        FROM price_history WHERE sub_type='{SUBTYPE}' AND market_price>0 GROUP BY product_id)
      SELECT product_id FROM s WHERE c>=60 AND lp BETWEEN 3 AND 3000 AND mx>mn
      ORDER BY (mx-mn)/av ASC""").fetchall()
    if not pids:
        con.close(); return []
    step = max(1, len(pids) // n)
    sel = [pids[i][0] for i in range(0, len(pids), step)][:n]
    out = []
    for pid in sel:
        rows = con.execute("SELECT date, market_price FROM price_history WHERE product_id=? AND sub_type=? "
                           "AND market_price>0 ORDER BY date ASC", [pid, SUBTYPE]).fetchall()
        if len(rows) < H + 20:
            continue
        dates = [datetime.strptime(r[0], "%Y-%m-%d").date() for r in rows]
        prices = [float(r[1]) for r in rows]
        out.append((dates, prices))
    con.close()
    return out


def feat(card):
    """Per-card features at the forecast origin (context = all but last H)."""
    dates, prices = card
    cd, cp = dates[:-H], prices[:-H]
    if len(cp) < 5:
        return None
    mu, sigma = served_params(cd, cp)
    if mu is None:
        return None
    S0 = cp[-1]; actual = prices[-H:]
    absf, lowf = [], []
    for h in range(1, H + 1):
        point = S0 * math.exp(mu * h / 365.0)
        absf.append(abs(actual[h - 1] - point) / S0)
        lowf.append((point - actual[h - 1]) / S0)
    return {"sigma": sigma, "mu": mu, "S0": S0, "actual": actual, "absf": absf, "lowf": lowf}


def fit_bundle(feats):
    """Conformal offsets from a set of cards: bands (symmetric) + var95/var99 (lower).
    The deep-tail lines carry a small safety margin (fit at 0.96 / 0.993, not
    0.95 / 0.99) so OOS exceedance lands at/under nominal per regime — a SOLD VaR
    must never under-protect. Bands stay at their nominal coverage."""
    absM = [[f["absf"][h] for f in feats] for h in range(H)]
    lowM = [[f["lowf"][h] for f in feats] for h in range(H)]
    bands = {L: [round(conf_q(absM[h], cov), 6) for h in range(H)] for L, cov in BANDS_ALL.items()}
    var95 = [round(conf_q(lowM[h], 0.96), 6) for h in range(H)]    # 0.96 -> ~<=5% OOS
    var99 = [round(conf_q(lowM[h], 0.993), 6) for h in range(H)]   # 0.993 -> ~<=1% OOS
    return {"bands": bands, "var95": var95, "var99": var99}


def regime_of(sigma, th):
    return "calm" if sigma <= th[0] else ("medium" if sigma <= th[1] else "jumpy")


def validate(feats, bundles, th, Hval):
    """Per-regime OOS coverage 80/90/95 + VaR95/VaR99 exceedance."""
    agg = {rg: {"c80": 0, "c90": 0, "c95": 0, "v95": 0, "v99": 0, "n": 0} for rg in REGIMES}
    for f in feats:
        rg = regime_of(f["sigma"], th); b = bundles[rg]; S0 = f["S0"]
        for h in range(1, Hval + 1):
            point = S0 * math.exp(f["mu"] * h / 365.0); a = f["actual"][h - 1]; agg[rg]["n"] += 1
            for L, key in (("0.80", "c80"), ("0.90", "c90"), ("0.95", "c95")):
                if point - b["bands"][L][h - 1] * S0 <= a <= point + b["bands"][L][h - 1] * S0:
                    agg[rg][key] += 1
            if a < point - b["var95"][h - 1] * S0: agg[rg]["v95"] += 1
            if a < point - b["var99"][h - 1] * S0: agg[rg]["v99"] += 1
    return agg


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="/tmp/mm_readonly.sqlite")
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--n", type=int, default=2000)
    a = ap.parse_args()

    cards = load(a.db, a.n)
    feats = [x for x in (feat(c) for c in cards) if x]
    if len(feats) < 120:
        print(f"❌ only {len(feats)} usable cards — too few"); sys.exit(1)
    calib = feats[0::2]; evalf = feats[1::2]

    # regime thresholds: terciles of CALIB sigma_annual (the value serve buckets on)
    sig = sorted(f["sigma"] for f in calib)
    t1 = round(sig[len(sig) // 3], 4); t2 = round(sig[2 * len(sig) // 3], 4)
    th = [t1, t2]

    # fit per-regime bundles (on calib) + global fallback
    bundles = {rg: fit_bundle([f for f in calib if regime_of(f["sigma"], th) == rg] or calib)
               for rg in REGIMES}
    glob = fit_bundle(calib)

    # ── validate OOS ──
    print(f"[conformal-regime] calib={len(calib)} eval={len(evalf)} | thresholds sigma_annual t1={t1} t2={t2} (db={a.db})")
    for Hval in (14, 30):
        agg = validate(evalf, bundles, th, Hval)
        print(f"  --- horizon {Hval}d ---  (targets cov ~80/90/95, VaR95<=~5, VaR99<=~1)")
        print(f"  {'regime':<8}{'n_cards':>8}{'cov80':>8}{'cov90':>8}{'cov95':>8}{'VaR95':>8}{'VaR99':>8}{'VaR95off@h':>12}")
        for rg in REGIMES:
            g = agg[rg]; nn = max(g["n"], 1)
            off14 = bundles[rg]["var95"][min(Hval, H) - 1]
            print(f"  {rg:<8}{g['n']//Hval:>8}{100*g['c80']/nn:>7.1f}%{100*g['c90']/nn:>7.1f}%"
                  f"{100*g['c95']/nn:>7.1f}%{100*g['v95']/nn:>7.1f}%{100*g['v99']/nn:>7.1f}%{off14:>12.4f}")
    print(f"  band-width check (var95 @h=14): calm {bundles['calm']['var95'][13]:.4f}  "
          f"medium {bundles['medium']['var95'][13]:.4f}  jumpy {bundles['jumpy']['var95'][13]:.4f}")

    out = {
        "fit_date": os.environ.get("CONFORMAL_FIT_DATE") or date.today().isoformat(),
        "base": "drift", "n_cards": len(calib), "max_horizon": H,
        "regime_thresholds": {"sigma_annual": th},
        "regimes": {rg: {"bands": {L: bundles[rg]["bands"][L] for L in BANDS_STORE},
                         "var95": bundles[rg]["var95"], "var99": bundles[rg]["var99"]} for rg in REGIMES},
        # global fallback (used when the card's sigma is unavailable at serve time)
        "bands": {L: glob["bands"][L] for L in BANDS_STORE},
        "var95": glob["var95"], "var99": glob["var99"],
    }
    with open(a.out, "w") as f:
        json.dump(out, f, indent=2)
    print(f"\n✅ wrote {a.out} (regime-aware: calm/medium/jumpy + global fallback, n_cards={len(calib)})")


if __name__ == "__main__":
    main()
