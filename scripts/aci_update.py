#!/usr/bin/env python3
"""
aci_update.py — AgACI adaptive-calibration layer over the conformal offsets.

WHY (deep-research 2026-07-13, docs/research/conformal_sota_2026-07-13.md):
split-conformal's guarantee breaks under DISTRIBUTION SHIFT (regime jumps) —
coverage can drop 90%→~84% on a mean shift. ACI (Gibbs & Candès 2021) provably
restores long-run coverage under arbitrary shift by online-updating the
miscoverage target from realized errors; AgACI (Zaffran et al. 2022) removes
the step-size choice by aggregating a grid of gamma "experts".

THIS implementation is the width-scaling variant adapted to our delayed-
feedback, nightly-batch system:
  - state: w[regime][level] = multiplicative width factor (start 1.0), one per
    gamma expert, plus expert weights.
  - nightly update: join forecast_ledger to realized prices (price_history at
    forecast_date+h) for NEWLY matured (forecast_date, horizon) cohorts; per
    regime×level compute realized miscoverage err; each gamma expert updates
      w *= exp(gamma * (err - alpha))        (undercover → widen, over → tighten)
    then experts are weighted by recent |coverage - nominal| (AgACI-lite).
  - output: aci_adjust.json {regime: {level: w_eff}} — the aggregated factor.
  - serving (server._conformal_forecast) multiplies band/VaR offsets by w_eff
    when the file exists and is fresh (<7d). Absent file = pure static conformal
    (fully non-destructive fallback).
  - w_eff clamped to [0.6, 2.0] — adaptation, never runaway bands.

BACKTEST MODE (--backtest): replays the ledger chronologically with honest
delayed feedback (a forecast made on day D at horizon h only informs the state
from day D+h onward) and reports static vs adaptive coverage per regime/level.
Run this BEFORE trusting the layer; wire the cron only if it helps.
"""
import os, json, argparse, sqlite3, math
from datetime import date, datetime, timedelta
from collections import defaultdict

X = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LEDGER = os.path.join(X, "forecast_ledger.sqlite")
MKT = os.path.expanduser("~/Documents/undesirables-mcp-server/.cache/market_memory.sqlite")
STATE_PATH = os.path.join(X, "aci_state.json")
ADJUST_PATH = os.path.join(X, "aci_adjust.json")

GAMMAS = [0.01, 0.03, 0.08, 0.2]          # expert grid (log-width step sizes)
LEVELS = {"band50": 0.50, "band90": 0.90, "var95": 0.95, "var99": 0.99}
REGIMES = ["calm", "medium", "jumpy"]
CLAMP = (0.6, 2.0)
WEIGHT_DECAY = 0.9                          # AgACI-lite: recency-weighted expert scoring


def fresh_state():
    return {"updated": None, "last_matured": "1970-01-01",
            "w": {rg: {lv: {str(g): 1.0 for g in GAMMAS} for lv in LEVELS} for rg in REGIMES},
            "score": {rg: {lv: {str(g): 0.0 for g in GAMMAS} for lv in LEVELS} for rg in REGIMES}}


def load_state():
    try:
        return json.load(open(STATE_PATH))
    except Exception:
        return fresh_state()


def actuals_for(mkt, pids_dates):
    """Realized price per (product_id, date) — max market_price that day."""
    out = {}
    cur = mkt.cursor()
    for pid, d in pids_dates:
        r = cur.execute("SELECT MAX(market_price) FROM price_history WHERE product_id=? AND date=?",
                        (pid, d)).fetchone()
        if r and r[0]:
            out[(pid, d)] = float(r[0])
    return out


def cohort_errors(led, mkt, fdate, horizon):
    """For one (forecast_date, horizon) cohort: per regime×level hit/miss counts,
    for BOTH static bands (as served) and a callable width-scaler."""
    rows = led.execute(
        "SELECT product_id, current_price, point, band_50_low, band_50_high, band_90_low, "
        "band_90_high, var95_pct, var99_pct, regime FROM forecast_ledger "
        "WHERE forecast_date=? AND horizon=? AND drift_spike=0", (fdate, horizon)).fetchall()
    if not rows:
        return None
    target = (date.fromisoformat(fdate) + timedelta(days=horizon)).isoformat()
    acts = actuals_for(mkt, [(r[0], target) for r in rows])
    out = []
    for pid, cp, point, b50l, b50h, b90l, b90h, v95p, v99p, rg in rows:
        a = acts.get((pid, target))
        if not a or not cp or not point:
            continue
        out.append({"regime": rg or "medium", "actual": a, "point": point, "cp": cp,
                    "hw50": (b50h - b50l) / 2 if b50h and b50l else None,
                    "hw90": (b90h - b90l) / 2 if b90h and b90l else None,
                    "var95": cp * (1 + (v95p or 0) / 100),   # VaR level in price terms
                    "var99": cp * (1 + (v99p or 0) / 100)})
    return out


def covered(f, level, w):
    """Hit test at width-scale w for one forecast row."""
    if level == "band50":
        return f["hw50"] is not None and abs(f["actual"] - f["point"]) <= f["hw50"] * w
    if level == "band90":
        return f["hw90"] is not None and abs(f["actual"] - f["point"]) <= f["hw90"] * w
    # VaR: actual must stay ABOVE the (scaled-down) floor. Scaling widens the
    # protective distance below the point: floor' = point - (point - floor)*w
    key = "var95" if level == "var95" else "var99"
    floor = f["point"] - (f["point"] - f[key]) * w
    return f["actual"] >= floor


def update_state(state, cohort):
    """One AgACI batch update from a matured cohort's forecasts."""
    by_rg = defaultdict(list)
    for f in cohort:
        by_rg[f["regime"]].append(f)
    for rg, fs in by_rg.items():
        if rg not in state["w"] or len(fs) < 20:      # skip tiny cohorts (noise)
            continue
        for lv, alpha in LEVELS.items():
            for g in GAMMAS:
                gk = str(g)
                w = state["w"][rg][lv][gk]
                hits = sum(1 for f in fs if covered(f, lv, w))
                n = sum(1 for f in fs if (lv.startswith("band") and f["hw" + lv[4:]] is not None) or lv.startswith("var"))
                if not n:
                    continue
                err = 1 - hits / n                      # realized miscoverage at this expert's width
                # ACI width update: undercover (err > 1-alpha_target) → widen
                target_err = 1 - alpha
                w_new = w * math.exp(g * (err - target_err))
                state["w"][rg][lv][gk] = min(CLAMP[1], max(CLAMP[0], w_new))
                # expert scoring: recency-weighted distance from nominal coverage
                state["score"][rg][lv][gk] = (WEIGHT_DECAY * state["score"][rg][lv][gk]
                                              + (1 - WEIGHT_DECAY) * abs(err - target_err))


def effective_w(state):
    """AgACI-lite aggregation: inverse-score-weighted mean of the gamma experts."""
    out = {}
    for rg in REGIMES:
        out[rg] = {}
        for lv in LEVELS:
            ws, wts = [], []
            for g in GAMMAS:
                gk = str(g)
                sc = state["score"][rg][lv][gk]
                wts.append(1.0 / (sc + 0.01))
                ws.append(state["w"][rg][lv][gk])
            tot = sum(wts)
            out[rg][lv] = round(min(CLAMP[1], max(CLAMP[0], sum(a * b for a, b in zip(ws, wts)) / tot)), 4)
    return out


def matured_cohorts(led, after, max_date):
    """(forecast_date, horizon) pairs newly matured: forecast_date+h <= max_date."""
    pairs = led.execute("SELECT DISTINCT forecast_date, horizon FROM forecast_ledger "
                        "ORDER BY forecast_date, horizon").fetchall()
    out = []
    for fd, h in pairs:
        mat = (date.fromisoformat(fd) + timedelta(days=h)).isoformat()
        if mat <= max_date and mat > after:
            out.append((fd, h, mat))
    return sorted(out, key=lambda x: x[2])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--backtest", action="store_true", help="replay the whole ledger; report static vs adaptive; no state/adjust writes")
    a = ap.parse_args()
    led = sqlite3.connect(f"file:{LEDGER}?mode=ro", uri=True)
    mkt = sqlite3.connect(f"file:{MKT}?mode=ro", uri=True)
    max_date = mkt.execute("SELECT MAX(date) FROM price_history WHERE product_id<9500000").fetchone()[0]

    if a.backtest:
        state = fresh_state()
        stats = {rg: {lv: {"static": [0, 0], "adaptive": [0, 0]} for lv in LEVELS} for rg in REGIMES}
        for fd, h, mat in matured_cohorts(led, "1970-01-01", max_date):
            cohort = cohort_errors(led, mkt, fd, h)
            if not cohort:
                continue
            w_eff = effective_w(state)                 # state as-of BEFORE this cohort matured
            for f in cohort:
                rg = f["regime"] if f["regime"] in stats else "medium"
                for lv in LEVELS:
                    if lv.startswith("band") and f["hw" + lv[4:]] is None:
                        continue
                    stats[rg][lv]["static"][0] += covered(f, lv, 1.0)
                    stats[rg][lv]["static"][1] += 1
                    stats[rg][lv]["adaptive"][0] += covered(f, lv, w_eff[rg][lv])
                    stats[rg][lv]["adaptive"][1] += 1
            update_state(state, cohort)                # then learn from it (honest delay)
        print(f"═══ BACKTEST static vs AgACI-adaptive (ledger replay, honest delayed feedback) ═══")
        for rg in REGIMES:
            print(f"  {rg}:")
            for lv, alpha in LEVELS.items():
                st, ad = stats[rg][lv]["static"], stats[rg][lv]["adaptive"]
                if not st[1]:
                    continue
                s, d = st[0] / st[1], ad[0] / ad[1]
                print(f"    {lv:<7} nominal {alpha:.2f} | static {s:.3f} | adaptive {d:.3f} | n={st[1]}"
                      + ("   ← closer" if abs(d - alpha) < abs(s - alpha) else ""))
        print("  final effective w:", json.dumps(effective_w(state)))
        return

    # ── nightly incremental update ──
    state = load_state()
    news = matured_cohorts(led, state.get("last_matured", "1970-01-01"), max_date)
    n_used = 0
    for fd, h, mat in news:
        cohort = cohort_errors(led, mkt, fd, h)
        if cohort:
            update_state(state, cohort)
            n_used += len(cohort)
        state["last_matured"] = max(state.get("last_matured", ""), mat)
    state["updated"] = datetime.now().isoformat(timespec="seconds")
    json.dump(state, open(STATE_PATH, "w"))
    adj = {"updated": state["updated"], "w": effective_w(state)}
    json.dump(adj, open(ADJUST_PATH, "w"))
    print(f"[aci] {len(news)} newly-matured cohort(s), {n_used} forecasts absorbed | w_eff: "
          + json.dumps(adj["w"]))


if __name__ == "__main__":
    main()
