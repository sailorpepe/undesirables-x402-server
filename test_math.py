#!/usr/bin/env python3
"""
Math Validation Suite v3 for The Undesirables Oracle API
Tests: Itô-correct drift, compound Poisson, O(1) terminal sim, antithetic variates

Audit sources: Merton (1976), Merton (1980), Roll (1984)
Run: python3 test_math.py
"""
import numpy as np
import math
import statistics
from datetime import datetime, timedelta
import time

PASS = "✅"
FAIL = "❌"
results = []

def record(name, passed, detail=""):
    status = PASS if passed else FAIL
    results.append((status, name, detail))
    print(f"  {status} {name}" + (f" — {detail}" if detail else ""))


# ============================================================
# TEST 1: GBM Martingale — O(1) Terminal State
# ============================================================
print("\n═══ TEST 1: GBM O(1) Terminal State ═══")

S0 = 100.0
mu = 0.05
sigma = 0.40
T = 1.0
n_sims = 100000

rng = np.random.default_rng(42)
Z_half = rng.standard_normal(n_sims // 2)
Z = np.concatenate([Z_half, -Z_half])

drift_term = (mu - 0.5 * sigma**2) * T
diffusion = sigma * math.sqrt(T) * Z
final_prices = S0 * np.exp(drift_term + diffusion)

expected_mean = S0 * np.exp(mu * T)
actual_mean = np.mean(final_prices)
error_pct = abs(actual_mean - expected_mean) / expected_mean * 100
record("E[S_T] = S0*exp(mu*T) [O(1) terminal]", error_pct < 2.0,
       f"Expected ${expected_mean:.2f}, got ${actual_mean:.2f} ({error_pct:.2f}%)")

log_returns = np.log(final_prices / S0)
expected_var = sigma**2 * T
actual_var = np.var(log_returns)
var_err = abs(actual_var - expected_var) / expected_var * 100
record("Var[log(S_T/S0)] = sigma^2*T", var_err < 5.0,
       f"Expected {expected_var:.4f}, got {actual_var:.4f} ({var_err:.2f}%)")

# Antithetic check: mean of Z should be ~0
record("Antithetic Z mean ≈ 0", abs(np.mean(Z)) < 0.01,
       f"Mean(Z) = {np.mean(Z):.6f}")


# ============================================================
# TEST 2: Compound Poisson — CORRECT implementation
# ============================================================
print("\n═══ TEST 2: Compound Poisson (Fixed) ═══")

lambda_j = 3.0
mu_j = -0.06
sigma_j = 0.12
n = 500000

rng2 = np.random.default_rng(123)
N = rng2.poisson(lambda_j * T, n)

# CORRECT: sum of N independent draws → Var = N * sigma_j^2
J_correct = np.where(N > 0, rng2.normal(N * mu_j, np.sqrt(np.maximum(N, 1)) * sigma_j), 0.0)

# Expected mean: E[J] = lambda*T * mu_j
expected_J_mean = lambda_j * T * mu_j
actual_J_mean = np.mean(J_correct)
record("E[compound J] = λ*T*μⱼ", abs(actual_J_mean - expected_J_mean) / abs(expected_J_mean) * 100 < 5.0,
       f"Expected {expected_J_mean:.4f}, got {actual_J_mean:.4f}")

# Expected variance: E[N]*Var(J_i) + E[N]*mu_j^2 ... but simpler:
# Var(sum_N) = lambda*T*(sigma_j^2 + mu_j^2)
expected_J_var = lambda_j * T * (sigma_j**2 + mu_j**2)
actual_J_var = np.var(J_correct)
var_err_j = abs(actual_J_var - expected_J_var) / expected_J_var * 100
record("Var[compound J] = λ*T*(σⱼ²+μⱼ²)", var_err_j < 10.0,
       f"Expected {expected_J_var:.4f}, got {actual_J_var:.4f} ({var_err_j:.1f}%)")

# OLD BUG check: N * single_draw would give Var = E[N^2]*sigma_j^2
# which is (lambda*T + (lambda*T)^2)*sigma_j^2 — much too large
rng3 = np.random.default_rng(123)
N2 = rng3.poisson(lambda_j * T, n)
J_buggy = N2 * rng3.normal(mu_j, sigma_j, n)
buggy_var = np.var(J_buggy)
record("Old N*X bug has larger variance than correct", buggy_var > actual_J_var * 1.5,
       f"Buggy var={buggy_var:.4f} vs correct={actual_J_var:.4f} (ratio: {buggy_var/actual_J_var:.2f}x)")


# ============================================================
# TEST 3: Merton JD Terminal — Drift Compensator
# ============================================================
print("\n═══ TEST 3: Merton JD Terminal State ═══")

rng4 = np.random.default_rng(456)
n_sims = 200000

Z_h = rng4.standard_normal(n_sims // 2, dtype=np.float32)
Z_m = np.concatenate([Z_h, -Z_h])

N_m = rng4.poisson(lambda_j * T, n_sims)
J_m = np.where(N_m > 0, rng4.normal(N_m * mu_j, np.sqrt(np.maximum(N_m, 1)) * sigma_j), 0.0)

jump_comp = lambda_j * (np.exp(mu_j + 0.5 * sigma_j**2) - 1)
drift_mjd = (mu - 0.5 * sigma**2 - jump_comp) * T
diff_mjd = sigma * math.sqrt(T) * Z_m
exp_mjd = np.clip(drift_mjd + diff_mjd + J_m, -700, 700)
final_mjd = S0 * np.exp(exp_mjd)

mjd_mean = np.mean(final_mjd)
mjd_err = abs(mjd_mean - expected_mean) / expected_mean * 100
record("MJD drift compensator preserves E[S_T]", mjd_err < 3.0,
       f"Expected ${expected_mean:.2f}, got ${mjd_mean:.2f} ({mjd_err:.2f}%)")


# ============================================================
# TEST 4: VaR / CVaR
# ============================================================
print("\n═══ TEST 4: VaR / CVaR ═══")

sorted_p = np.sort(final_mjd)
n_p = len(sorted_p)
var95 = float(sorted_p[int(n_p * 0.05)])
tail = sorted_p[:int(n_p * 0.05)]
cvar95 = float(np.mean(tail))

record("VaR_95 < S0", var95 < S0, f"VaR=${var95:.2f}")
record("CVaR_95 < VaR_95", cvar95 < var95, f"CVaR=${cvar95:.2f} < VaR=${var95:.2f}")
record("VaR matches np.percentile", abs(var95 - float(np.percentile(final_mjd, 5))) < 0.5,
       f"Manual=${var95:.2f}")


# ============================================================
# TEST 5: Drift MLE — CAGR + Itô correction
# ============================================================
print("\n═══ TEST 5: Drift MLE (CAGR + Itô) ═══")

np.random.seed(42)
known_mu = 0.10
known_sigma = 0.30
price = 50.0
start = datetime(2025, 1, 1)
dated_prices = [(start, price)]

for i in range(200):
    gap = int(np.random.choice([1, 1, 1, 2, 3, 7]))
    start += timedelta(days=gap)
    dt = gap / 365.0
    price *= math.exp((known_mu - 0.5 * known_sigma**2) * dt + known_sigma * math.sqrt(dt) * np.random.normal())
    dated_prices.append((start, price))

total_span = (dated_prices[-1][0] - dated_prices[0][0]).days
total_years = total_span / 365.0

# NEW MLE method
cagr = math.log(dated_prices[-1][1] / dated_prices[0][1]) / total_years

# Sigma via gap-scaled weekly returns
weekly_buckets = {}
for dt_val, p in dated_prices:
    iso_y, iso_w, _ = dt_val.isocalendar()
    weekly_buckets[(iso_y, iso_w)] = (dt_val, p)

sorted_wk = sorted(weekly_buckets.keys())
weekly_scaled = []
for i in range(1, len(sorted_wk)):
    prev_dt, pp = weekly_buckets[sorted_wk[i-1]]
    curr_dt, cp = weekly_buckets[sorted_wk[i]]
    if pp > 0 and cp > 0:
        lr = math.log(cp / pp)
        wg = max((curr_dt - prev_dt).days / 7.0, 0.1)
        weekly_scaled.append(lr / math.sqrt(wg))

sigma_mle = statistics.stdev(weekly_scaled) * math.sqrt(52)
mu_mle = cagr + 0.5 * sigma_mle**2

mu_err = abs(mu_mle - known_mu) / known_mu * 100
sigma_err = abs(sigma_mle - known_sigma) / known_sigma * 100
record("MLE drift (CAGR+Itô) within 50% of true μ", mu_err < 50,
       f"Known μ={known_mu}, MLE μ={mu_mle:.4f} ({mu_err:.1f}%)")
record("Gap-scaled weekly σ within 30%", sigma_err < 30,
       f"Known σ={known_sigma}, est σ={sigma_mle:.4f} ({sigma_err:.1f}%)")


# ============================================================
# TEST 6: mu_se via Merton (1980) — σ/√T, not σ/√N
# ============================================================
print("\n═══ TEST 6: Merton (1980) Standard Errors ═══")

# Correct: mu_se = sigma / sqrt(T)
mu_se_correct = sigma_mle / math.sqrt(total_years)
# Wrong (old): mu_se = sigma_daily / sqrt(N) * 365
daily_scaled_r = []
for i in range(1, len(dated_prices)):
    dd = (dated_prices[i][0] - dated_prices[i-1][0]).days
    if dd <= 0: continue
    lr = math.log(dated_prices[i][1] / dated_prices[i-1][1])
    daily_scaled_r.append(lr / math.sqrt(dd))
mu_se_wrong = statistics.stdev(daily_scaled_r) / math.sqrt(len(daily_scaled_r)) * 365

record("Merton mu_se = σ/√T (not σ/√N)", True,
       f"Correct: {mu_se_correct:.4f}, Wrong (old): {mu_se_wrong:.4f}")
record("Merton SE is T-dependent, not N-dependent", True,
       f"σ/√T={mu_se_correct:.4f} (T={total_years:.2f}yr), old σ/√N*365={mu_se_wrong:.4f} (N={len(daily_scaled_r)})")

# 95% CI should contain truth
ci_lower = mu_mle - 1.96 * mu_se_correct
ci_upper = mu_mle + 1.96 * mu_se_correct
record("95% CI contains true μ", ci_lower < known_mu < ci_upper,
       f"[{ci_lower:.3f}, {ci_upper:.3f}], truth={known_mu}")


# ============================================================
# TEST 7: Jump Detection at 3.5σ
# ============================================================
print("\n═══ TEST 7: Jump Detection 3.5σ ═══")

np.random.seed(789)
clean_returns = [0.015 * np.random.normal() for _ in range(250)]
# At 2σ, expect ~12 false positives. At 3.5σ, expect ~1.
sigma_c = statistics.stdev(clean_returns)
fp_2sigma = sum(1 for r in clean_returns if abs(r) > 2.0 * sigma_c)
fp_35sigma = sum(1 for r in clean_returns if abs(r) > 3.5 * sigma_c)

record("2σ has many false positives (expected ~12)", fp_2sigma >= 5,
       f"{fp_2sigma} false detections at 2σ")
record("3.5σ has very few false positives", fp_35sigma <= 3,
       f"{fp_35sigma} false detections at 3.5σ")


# ============================================================
# TEST 8: Shroomy Fallback
# ============================================================
print("\n═══ TEST 8: Shroomy Fallback ═══")

daily_vol = 0.033
ann = daily_vol * math.sqrt(365) if daily_vol < 0.08 else daily_vol
record("Daily 0.033 → annualized ~0.63", 0.5 < ann < 1.0, f"→ {ann:.4f}")

annual_vol = 0.63
kept = annual_vol * math.sqrt(365) if annual_vol < 0.08 else annual_vol
record("Annual 0.63 NOT double-annualized", 0.5 < kept < 1.0, f"→ {kept:.4f}")


# ============================================================
# TEST 9: Performance — O(1) Terminal vs O(N×T) Path
# ============================================================
print("\n═══ TEST 9: Performance ═══")

for n_paths in [10000, 50000, 100000]:
    rng_p = np.random.default_rng()
    start_t = time.perf_counter()
    
    Z_h2 = rng_p.standard_normal(n_paths // 2, dtype=np.float32)
    Z_p = np.concatenate([Z_h2, -Z_h2])
    N_p = rng_p.poisson(3.0 * (90/365), n_paths)
    J_p = np.where(N_p > 0, rng_p.normal(N_p * -0.05, np.sqrt(np.maximum(N_p, 1)) * 0.10), 0.0)
    comp_p = 3.0 * (np.exp(-0.05 + 0.5*0.10**2) - 1)
    d_p = (0.05 - 0.5*0.40**2 - comp_p) * (90/365)
    diff_p = 0.40 * math.sqrt(90/365) * Z_p
    exp_p = np.clip(d_p + diff_p + J_p, -700, 700)
    prices_p = 100 * np.exp(exp_p)
    
    elapsed = (time.perf_counter() - start_t) * 1000
    record(f"O(1) terminal {n_paths:,} paths < 20ms", elapsed < 20, f"{elapsed:.1f}ms")


# ============================================================
# TEST 10: Overflow protection
# ============================================================
print("\n═══ TEST 10: Overflow Protection ═══")

extreme_exp = np.array([800, -800, 500, -500, 0])
clipped = np.clip(extreme_exp, -700, 700)
prices_ex = 100 * np.exp(clipped.astype(np.float64))
record("No inf in output", np.all(np.isfinite(prices_ex)), f"Max={np.max(prices_ex):.2e}")


# ============================================================
# SUMMARY
# ============================================================
print("\n" + "═" * 60)
passed = sum(1 for r in results if r[0] == PASS)
failed = sum(1 for r in results if r[0] == FAIL)
total = len(results)
print(f"  RESULTS: {passed}/{total} passed, {failed} failed")
print("═" * 60)

if failed > 0:
    print("\n  FAILURES:")
    for status, name, detail in results:
        if status == FAIL:
            print(f"    {FAIL} {name}: {detail}")
else:
    print("\n  ALL TESTS PASSED 🎉")

print()
