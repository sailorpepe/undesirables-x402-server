#!/usr/bin/env python3
"""
card_grades.py — two pure letter-grade functions for the forecast product.

  SAFE-HOLD  : capital preservation, from the calibrated 95% VaR (+ a 99% fat-tail
               guard). ABSOLUTE scale — never graded on a curve. "A+ = <=5%" must
               always mean genuinely-low risk; 5% means 5%.
  MOMENTUM   : direction, from the 30-day expected move (point/S0 - 1) gated by
               prob_up as a conviction filter. C = genuinely flat (+-3%). Drift is
               the noisiest part of the model -> treat MOMENTUM as the softer signal.

Cut-points validated 2026-06-18 against forecast_ledger.sqlite (model=conformal,
2000 cards, 30d horizon). Deterministic; stdlib only (no random/scipy/sklearn);
no Heston/Kou. NOT wired into server.py / the /card page yet — that's the next
step once the cut-points are blessed.

Inputs are the signed percents the conformal forecast already emits:
  var95_pct, var99_pct : e.g. -8.3 means a 5% chance of an 8.3% drop (negative).
  expected_move_pct    : (point / current_price - 1) * 100.
  prob_up              : probability of gain, 0..1.
"""
import os
import sqlite3
import sys

_DROP = {"A+": "A", "A": "B", "B": "C", "C": "D", "D": "F", "F": "F"}


def safe_hold_grade(var95_pct: float, var99_pct: float) -> str:
    """Downside / capital-preservation grade from the 95% VaR loss. ABSOLUTE bands;
    a deep 99% tail (var99_pct <= -60) drops one letter.

    Uses DOWNSIDE LOSS = max(0, -var95_pct), not abs(): a positive VaR (the 5th
    percentile is still a gain) means no modeled downside -> A+, whereas abs() would
    misread it as huge risk. Identical to |var95_pct| for all normal (negative-VaR)
    cards. NOTE: ~1.5% of cards are drift-exploded (point forecast runs away on
    recently-spiked names) and produce a positive VaR — cap drift upstream / filter
    them before trusting grades on those names (see report)."""
    loss = max(0.0, -var95_pct)
    g = ("A+" if loss <= 5 else "A" if loss <= 8 else "B" if loss <= 12
         else "C" if loss <= 20 else "D" if loss <= 35 else "F")
    if var99_pct <= -60:                       # fat-tail guard
        g = _DROP[g]
    return g


def momentum_grade(expected_move_pct: float, prob_up: float) -> str:
    """Direction grade from the 30-day expected move, gated by prob_up conviction.
    C stays genuinely flat (+-3%); a positive move with weak conviction caps at C;
    A+ requires strong conviction (prob_up >= 0.60)."""
    m = expected_move_pct
    g = ("A+" if m >= 15 else "A" if m >= 8 else "B" if m >= 3
         else "C" if m >= -3 else "D" if m >= -10 else "F")
    if m > 0 and prob_up < 0.55:               # positive but unconvinced -> flat
        return "C"
    if g == "A+" and prob_up < 0.60:           # A+ needs conviction
        return "A"
    return g


# ───────────────────────── tests + CLI ─────────────────────────
def _test():
    assert safe_hold_grade(-4.0, -20) == "A+"
    assert safe_hold_grade(-7.5, -22) == "A"
    assert safe_hold_grade(-11.0, -30) == "B"
    assert safe_hold_grade(-18.0, -40) == "C"
    assert safe_hold_grade(-30.0, -50) == "D"
    assert safe_hold_grade(-50.0, -90) == "F"
    assert safe_hold_grade(-4.0, -65) == "A"          # A+ dropped by tail guard
    assert safe_hold_grade(-18.0, -100) == "D"        # C dropped by tail guard
    assert safe_hold_grade(1240.4, 318.8) == "A+"     # positive VaR (no downside) -> A+, not F
    assert safe_hold_grade(0.1, -16.4) == "A+"        # tiny positive VaR, calm
    assert momentum_grade(20, 0.70) == "A+"
    assert momentum_grade(20, 0.58) == "A"            # A+ -> A (conviction < 0.60)
    assert momentum_grade(10, 0.60) == "A"
    assert momentum_grade(5, 0.60) == "B"
    assert momentum_grade(0, 0.50) == "C"
    assert momentum_grade(-6, 0.40) == "D"
    assert momentum_grade(-15, 0.30) == "F"
    assert momentum_grade(10, 0.50) == "C"            # positive + weak conviction -> C
    print("✓ all grade unit tests passed")


def _cli(product_id: int):
    db = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "forecast_ledger.sqlite")
    con = sqlite3.connect(f"file:{os.path.abspath(db)}?mode=ro", uri=True)
    r = con.execute(
        "SELECT card_name, current_price, point, var95_pct, var99_pct, prob_up, regime, forecast_date "
        "FROM forecast_ledger WHERE product_id=? AND horizon=30 ORDER BY forecast_date DESC LIMIT 1",
        [product_id]).fetchone()
    con.close()
    if not r:
        print(f"no 30d ledger row for product {product_id}"); return
    name, cur, pt, v95, v99, pup, reg, fd = r
    emove = (pt / cur - 1) * 100
    print(f"{name} (#{product_id})  ${cur:,.2f}  ·  regime {reg}  ·  as of {fd}")
    print(f"  expected 30d move {emove:+.1f}%  ·  prob_up {pup:.2f}  ·  VaR95 {v95:.1f}%  ·  VaR99 {v99:.1f}%")
    print(f"  SAFE-HOLD: {safe_hold_grade(v95, v99)}   MOMENTUM: {momentum_grade(emove, pup)}")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--test":
        _test()
    elif len(sys.argv) > 1:
        _cli(int(sys.argv[1]))
    else:
        _test()
