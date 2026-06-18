#!/usr/bin/env python3
"""
conformal_calibrate.py — fit split-conformal band/tail offsets for the
/api/v1/simulate?model=conformal endpoint and write conformal_offsets.json.

Validated logic (Round 5 conformalized-drift winner): per-step offsets fit on
real cross-card holdout residuals give calibrated coverage + honest VaR with NO
Monte Carlo. The point forecast EXACTLY matches the served endpoint:

    mu      = _get_calibrated_params() v3 drift on the CONTEXT (ISO-week
              gap-scaled sigma + CAGR + Ito), rounded to 4 dp
    point_h = current_price * exp(mu * h / 365)

Offsets are fractions of current_price, per step h=1..30:
    bands[L] = quantile_L(|actual - point| / price)        (symmetric half-width)
    varP     = quantile_P((point - actual) / price)         (one-sided lower tail)

Read-only on the DB (opened mode=ro). Fixed, deterministic card pick. Disjoint
calib/eval split. Validates OOS before writing.

Usage:
  python3 scripts/conformal_calibrate.py [--db PATH] [--out PATH] [--n 800]
  default db=/tmp/mm_readonly.sqlite  out=<next to server.py>/conformal_offsets.json
"""
import os, sys, math, sqlite3, json, argparse, statistics
from datetime import datetime, date

H = 30                       # max_horizon published
SUBTYPE = "Normal"
BANDS_STORE = ["0.50", "0.90"]              # what the endpoint reads
BANDS_ALL = {"0.50": 0.50, "0.80": 0.80, "0.90": 0.90, "0.95": 0.95}  # +0.80/0.95 for validation
HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_OUT = os.path.join(os.path.dirname(HERE), "conformal_offsets.json")


def served_mu(dates, prices):
    """EXACT replica of server.py _get_calibrated_params main path (mu_annual)."""
    span = (dates[-1] - dates[0]).days
    years = max(span / 365.0, 0.01)
    weekly = []
    if span >= 28:
        buckets = {}
        for d, p in zip(dates, prices):
            buckets[d.isocalendar()[:2]] = (d, p)          # (iso_year, iso_week) -> last of week
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
        return None
    cagr = math.log(prices[-1] / prices[0]) / years
    return round(cagr + 0.5 * sigma ** 2, 4)               # mu_annual (Ito), as the endpoint rounds


def conf_q(scores, cov):
    n = len(scores); lvl = min(1.0, math.ceil((n + 1) * cov) / n)
    return float(_quantile(sorted(scores), lvl))


def _quantile(s, lvl):       # 'higher' interpolation, matches np.quantile(method='higher')
    import bisect
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
    # deterministic stratified pick across movement
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


def residuals(card):
    """Return per-step (|err|/S0, (point-actual)/S0) for h=1..H, or None."""
    dates, prices = card
    cd, cp = dates[:-H], prices[:-H]
    if len(cp) < 5:
        return None
    mu = served_mu(cd, cp)
    if mu is None:
        return None
    S0 = cp[-1]; actual = prices[-H:]
    absf = []; lowf = []
    for h in range(1, H + 1):
        point = S0 * math.exp(mu * h / 365.0)
        absf.append(abs(actual[h - 1] - point) / S0)
        lowf.append((point - actual[h - 1]) / S0)
    return absf, lowf


def fit(cards):
    R = [r for r in (residuals(c) for c in cards) if r]
    absM = [[R[i][0][h] for i in range(len(R))] for h in range(H)]   # per-step abs frac
    lowM = [[R[i][1][h] for i in range(len(R))] for h in range(H)]   # per-step lower frac
    bands = {L: [conf_q(absM[h], cov) for h in range(H)] for L, cov in BANDS_ALL.items()}
    var95 = [conf_q(lowM[h], 0.95) for h in range(H)]
    var99 = [conf_q(lowM[h], 0.99) for h in range(H)]
    return bands, var95, var99, len(R)


def validate(cards, bands, var95, var99, Hval):
    """OOS: coverage at 80/90/95 + VaR95/VaR99 exceedance using fitted offsets."""
    cov_hit = {L: 0 for L in ("0.80", "0.90", "0.95")}; tot = 0
    v95 = v99 = 0
    for c in cards:
        dates, prices = c; cd, cp = dates[:-H], prices[:-H]
        mu = served_mu(cd, cp)
        if mu is None: continue
        S0 = cp[-1]; actual = prices[-H:]
        for h in range(1, Hval + 1):
            point = S0 * math.exp(mu * h / 365.0); a = actual[h - 1]; tot += 1
            for L in cov_hit:
                if point - bands[L][h - 1] * S0 <= a <= point + bands[L][h - 1] * S0:
                    cov_hit[L] += 1
            if a < point - var95[h - 1] * S0: v95 += 1
            if a < point - var99[h - 1] * S0: v99 += 1
    return {L: 100.0 * cov_hit[L] / tot for L in cov_hit}, 100.0 * v95 / tot, 100.0 * v99 / tot, tot


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="/tmp/mm_readonly.sqlite")
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--n", type=int, default=800)
    ap.add_argument("--fit-date", default=None, help="override fit_date (default: file mtime is avoided; pass YYYY-MM-DD)")
    a = ap.parse_args()

    cards = load(a.db, a.n)
    if len(cards) < 60:
        print(f"❌ only {len(cards)} cards — too few to calibrate"); sys.exit(1)
    # disjoint calib / eval (deterministic alternating split)
    calib = cards[0::2]; evalc = cards[1::2]
    bands, var95, var99, ncal = fit(calib)

    print(f"[conformal] calib cards={ncal}  eval cards={len(evalc)}  (db={a.db})")
    print(f"{'horizon':<10}{'cov80':>9}{'cov90':>9}{'cov95':>9}{'VaR95':>9}{'VaR99':>9}{'n':>9}")
    for Hval in (14, 30):
        cov, v95, v99, tot = validate(evalc, bands, var95, var99, Hval)
        print(f"{Hval:<10}{cov['0.80']:>8.1f}%{cov['0.90']:>8.1f}%{cov['0.95']:>8.1f}%"
              f"{v95:>8.1f}%{v99:>8.1f}%{tot:>9}")
    print("targets:   cov80~80  cov90~90  cov95~95  VaR95<=~5  VaR99<=~1")

    fit_date = a.fit_date or _today()
    out = {
        "fit_date": fit_date, "base": "drift", "n_cards": ncal, "max_horizon": H,
        "bands": {L: [round(x, 6) for x in bands[L]] for L in BANDS_STORE},
        "var95": [round(x, 6) for x in var95],
        "var99": [round(x, 6) for x in var99],
    }
    with open(a.out, "w") as f:
        json.dump(out, f, indent=2)
    print(f"\n✅ wrote {a.out}  (fit_date={fit_date}, base=drift, n_cards={ncal}, max_horizon={H})")


def _today():
    # avoid Date.now-style nondeterminism concerns: read the DB's latest date as 'as-of'
    return os.environ.get("CONFORMAL_FIT_DATE") or date.today().isoformat()


if __name__ == "__main__":
    main()
