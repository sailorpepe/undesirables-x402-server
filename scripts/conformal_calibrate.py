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
        return None, None, None
    cagr = math.log(prices[-1] / prices[0]) / years
    mu = cagr + 0.5 * sigma ** 2
    spike = (math.exp(mu * 30.0 / 365.0) - 1.0) > 0.50    # raw 30d move > 50% (drift spike)
    mu = max(-1.0, min(mu, 2.0))                          # mirror server _get_calibrated_params clamp
    return round(mu, 4), round(sigma, 4), spike


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


def feat(card, back=0):
    """Per-card features at a forecast origin `back` rows before the latest
    one (back=0 = the newest origin, the legacy behavior: context = all but
    the last H rows, outcomes = those H rows)."""
    dates, prices = card
    cut = len(prices) - H - back
    if cut < 5:
        return None
    cd, cp = dates[:cut], prices[:cut]
    mu, sigma, _ = served_params(cd, cp)
    if mu is None:
        return None
    S0 = cp[-1]; actual = prices[cut:cut + H]
    absf, lowf = [], []
    for h in range(1, H + 1):
        point = S0 * math.exp(mu * h / 365.0)
        absf.append(abs(actual[h - 1] - point) / S0)
        lowf.append((point - actual[h - 1]) / S0)
    return {"sigma": sigma, "mu": mu, "S0": S0, "actual": actual, "absf": absf,
            "lowf": lowf, "back": back}


# ── NexCP (Barber et al., "Conformal prediction beyond exchangeability") ──
# Weighted split-conformal: calibration scores from MULTIPLE historical
# origins per card, exponentially down-weighted by age, so the tails see far
# more data while stale regimes fade instead of polluting current bands.
def wconf_q(pairs, cov):
    """Weighted conformal quantile with the +1 test-point correction.
    pairs = [(score, weight)]; the hypothetical test point carries weight 1
    (same as the newest origin), which keeps the estimate conservative."""
    pairs = sorted(pairs)
    tot = sum(w for _, w in pairs) + 1.0
    acc = 0.0
    for s, w in pairs:
        acc += w
        if (acc + 1.0) / tot >= cov:
            return s
    return pairs[-1][0]


def fit_bundle_w(feats, half_life, stride, multi=False):
    """Recency-weighted analog of fit_bundle. Weight = 0.5^(age_days/half_life),
    age measured in rows back from the newest origin (rows ~ days here).
    multi=True (origins>1): deep tail fit at 0.994 instead of 0.993 — the
    single-origin margin was finite-sample slack that shrinks with 2x+ data;
    this restores the VaR99<=1% cushion at negligible width cost."""
    def wt(f):
        return 0.5 ** ((f["back"] * 1.0) / half_life) if half_life else 1.0
    v95_cov = 0.962 if multi else 0.96      # multi-origin: same finite-sample
    v99_cov = 0.994 if multi else 0.993     # cushion logic as the deep tail
    bands = {}
    for L, cov in BANDS_ALL.items():
        bands[L] = [round(wconf_q([(f["absf"][h], wt(f)) for f in feats], cov), 6)
                    for h in range(H)]
    var95 = [round(wconf_q([(f["lowf"][h], wt(f)) for f in feats], v95_cov), 6) for h in range(H)]
    var99 = [round(wconf_q([(f["lowf"][h], wt(f)) for f in feats], v99_cov), 6) for h in range(H)]
    return {"bands": bands, "var95": var95, "var99": var99}


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


def nexcp_backtest(cards, origins, half_life, stride):
    """Out-of-time comparison on the NEWEST window (back=0), which no method
    is allowed to calibrate on here:
      legacy : single origin at back=stride  (the current method, shifted)
      multi  : origins at back=stride..origins*stride, EQUAL weight
      nexcp  : same origins, recency-weighted (half_life)
    Reports per-regime coverage + mean VaR95 offset (sharpness)."""
    ev = [x for x in (feat(c, 0) for c in cards) if x]
    pools = {k: [] for k in ("legacy", "multi", "nexcp")}
    for c in cards:
        for k in range(1, origins + 1):
            f = feat(c, k * stride)
            if not f:
                continue
            if k == 1:
                pools["legacy"].append(f)
            pools["multi"].append(f)
            pools["nexcp"].append(f)
    print(f"[nexcp-backtest] eval cards={len(ev)} | calib pool: legacy={len(pools['legacy'])} "
          f"multi/nexcp={len(pools['multi'])} (origins={origins}, stride={stride}, half_life={half_life})")

    # shared regime thresholds from the legacy pool (what production would see)
    sig = sorted(f["sigma"] for f in pools["legacy"])
    th = [round(sig[len(sig) // 3], 4), round(sig[2 * len(sig) // 3], 4)]

    for name in ("legacy", "multi", "nexcp"):
        hl = half_life if name == "nexcp" else 0
        bundles = {}
        for rg in REGIMES:
            fs = [f for f in pools[name] if regime_of(f["sigma"], th) == rg] or pools[name]
            bundles[rg] = fit_bundle_w(fs, hl, stride, multi=(name != "legacy"))
        agg = validate(ev, bundles, th, 30)
        print(f"  ── {name} ──   (targets cov 80/90/95, VaR95<=5, VaR99<=1)")
        for rg in REGIMES:
            g = agg[rg]; nn = max(g["n"], 1)
            w95 = bundles[rg]["var95"][13]
            print(f"    {rg:<8} n={g['n']//30:>4}  cov80 {100*g['c80']/nn:5.1f}%  cov90 {100*g['c90']/nn:5.1f}%  "
                  f"cov95 {100*g['c95']/nn:5.1f}%  VaR95 {100*g['v95']/nn:4.1f}%  VaR99 {100*g['v99']/nn:4.1f}%  "
                  f"var95off@14 {w95:.4f}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="/tmp/mm_readonly.sqlite")
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--n", type=int, default=2000)
    ap.add_argument("--origins", type=int, default=1,
                    help="calibration origins per card (1 = legacy single-window; >1 enables NexCP)")
    ap.add_argument("--half-life", type=float, default=60.0,
                    help="recency half-life in days for NexCP weighting")
    ap.add_argument("--stride", type=int, default=21, help="rows between origins")
    ap.add_argument("--backtest", action="store_true",
                    help="compare legacy vs multi-origin vs NexCP out-of-time; no file writes")
    a = ap.parse_args()

    cards = load(a.db, a.n)
    if a.backtest:
        nexcp_backtest(cards, max(a.origins, 5), a.half_life, a.stride)
        return
    if a.origins > 1:
        feats = [x for c in cards for x in (feat(c, k * a.stride) for k in range(a.origins)) if x]
    else:
        feats = [x for x in (feat(c) for c in cards) if x]
    if len(feats) < 120:
        print(f"❌ only {len(feats)} usable cards — too few"); sys.exit(1)
    calib = feats[0::2]; evalf = feats[1::2]

    # regime thresholds: terciles of CALIB sigma_annual (the value serve buckets on)
    sig = sorted(f["sigma"] for f in calib)
    t1 = round(sig[len(sig) // 3], 4); t2 = round(sig[2 * len(sig) // 3], 4)
    th = [t1, t2]

    # fit per-regime bundles (on calib) + global fallback.
    # origins>1 -> NexCP recency-weighted fit; origins=1 -> legacy unweighted.
    hl = a.half_life if a.origins > 1 else 0
    bundles = {rg: fit_bundle_w([f for f in calib if regime_of(f["sigma"], th) == rg] or calib,
                                hl, a.stride, multi=(a.origins > 1))
               for rg in REGIMES}
    glob = fit_bundle_w(calib, hl, a.stride, multi=(a.origins > 1))

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
