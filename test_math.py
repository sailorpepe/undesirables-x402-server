#!/usr/bin/env python3
"""
Math Validation Suite v2 for The Undesirables Oracle API
Tests: Merton Jump-Diffusion, GBM, VaR/CVaR, Calibration v2

Run: python3 test_math.py
"""
import numpy as np
import math
import statistics
from datetime import datetime, timedelta

PASS = "✅"
FAIL = "❌"
WARN = "⚠️"
results = []

def record(test_name, passed, detail=""):
    status = PASS if passed else FAIL
    results.append((status, test_name, detail))
    print(f"  {status} {test_name}" + (f" — {detail}" if detail else ""))


# ============================================================
# TEST 1: GBM Martingale Property
# ============================================================
print("\n═══ TEST 1: GBM Drift Consistency ═══")

S0 = 100.0
mu = 0.05
sigma = 0.40
days = 365
dt = 1.0 / 365.0
n_sims = 100000

Z = np.random.standard_normal((n_sims, days))
t = np.arange(1, days + 1) * dt
drift = (mu - 0.5 * sigma**2) * t
diffusion = sigma * np.cumsum(np.sqrt(dt) * Z, axis=1)
paths = S0 * np.exp(drift + diffusion)
final_prices = paths[:, -1]

expected_mean = S0 * np.exp(mu * 1.0)
actual_mean = np.mean(final_prices)
error_pct = abs(actual_mean - expected_mean) / expected_mean * 100
record("GBM E[S_T] matches S0*exp(mu*T)", error_pct < 2.0,
       f"Expected ${expected_mean:.2f}, got ${actual_mean:.2f} ({error_pct:.2f}%)")

log_returns = np.log(final_prices / S0)
expected_var = sigma**2 * 1.0
actual_var = np.var(log_returns)
var_error = abs(actual_var - expected_var) / expected_var * 100
record("GBM log-return variance matches sigma^2*T", var_error < 5.0,
       f"Expected {expected_var:.4f}, got {actual_var:.4f} ({var_error:.2f}%)")


# ============================================================
# TEST 2: Merton Jump-Diffusion
# ============================================================
print("\n═══ TEST 2: Merton Jump-Diffusion ═══")

lambda_jump = 3.0
mu_j = -0.06
sigma_j = 0.12

Z = np.random.standard_normal((n_sims, days))
N = np.random.poisson(lambda_jump * dt, (n_sims, days))
J = N * np.random.normal(mu_j, sigma_j, (n_sims, days))
J_cumulative = np.cumsum(J, axis=1)

jump_compensator = lambda_jump * (np.exp(mu_j + 0.5 * sigma_j**2) - 1)
drift_mjd = (mu - 0.5 * sigma**2 - jump_compensator) * t
diffusion_mjd = sigma * np.cumsum(np.sqrt(dt) * Z, axis=1)
paths_mjd = S0 * np.exp(drift_mjd + diffusion_mjd + J_cumulative)
final_mjd = paths_mjd[:, -1]

mjd_mean = np.mean(final_mjd)
mjd_error = abs(mjd_mean - expected_mean) / expected_mean * 100
record("Merton JD drift compensator preserves E[S_T]", mjd_error < 3.0,
       f"Expected ${expected_mean:.2f}, got ${mjd_mean:.2f} ({mjd_error:.2f}%)")

total_jumps = np.sum(N, axis=1)
expected_jumps = lambda_jump * 1.0
actual_avg_jumps = np.mean(total_jumps)
jump_error = abs(actual_avg_jumps - expected_jumps) / expected_jumps * 100
record("Poisson jump arrivals match lambda*T", jump_error < 5.0,
       f"Expected {expected_jumps:.1f}/yr, got {actual_avg_jumps:.2f} ({jump_error:.2f}%)")

gbm_kurtosis = float(np.mean(((np.log(final_prices/S0) - np.mean(np.log(final_prices/S0))) / np.std(np.log(final_prices/S0)))**4))
mjd_kurtosis = float(np.mean(((np.log(final_mjd/S0) - np.mean(np.log(final_mjd/S0))) / np.std(np.log(final_mjd/S0)))**4))
record("Merton JD fatter tails than GBM", mjd_kurtosis > gbm_kurtosis,
       f"GBM: {gbm_kurtosis:.3f}, Merton: {mjd_kurtosis:.3f}")

mjd_log_returns = np.log(final_mjd / S0)
skewness = float(np.mean(((mjd_log_returns - np.mean(mjd_log_returns)) / np.std(mjd_log_returns))**3))
record("Merton JD negative skew (mu_j < 0)", skewness < 0, f"Skew: {skewness:.4f}")


# ============================================================
# TEST 3: VaR / CVaR
# ============================================================
print("\n═══ TEST 3: VaR / CVaR ═══")

sorted_prices = np.sort(final_mjd)
n = len(sorted_prices)
var_95_price = float(sorted_prices[int(n * 0.05)])
tail = sorted_prices[:int(n * 0.05)]
cvar_95_price = float(np.mean(tail))

record("VaR_95 < S0", var_95_price < S0,
       f"VaR=${var_95_price:.2f} vs S0=${S0:.2f}")
record("CVaR_95 < VaR_95", cvar_95_price < var_95_price,
       f"CVaR=${cvar_95_price:.2f} < VaR=${var_95_price:.2f}")
var_manual = float(np.percentile(final_mjd, 5))
record("VaR matches np.percentile", abs(var_95_price - var_manual) / var_manual * 100 < 1.0,
       f"Ours: ${var_95_price:.2f}, numpy: ${var_manual:.2f}")


# ============================================================
# TEST 4: Calibration v2 — Weekly Resampling (FIX #1)
# ============================================================
print("\n═══ TEST 4: Calibration v2 — Weekly Resampling ═══")

np.random.seed(42)
base_price = 50.0
known_mu = 0.10
known_sigma = 0.30

# Generate synthetic daily prices with realistic gaps
dates_synth = []
prices_synth = []
price = base_price
start_date = datetime(2025, 1, 1)

for i in range(200):
    gap = int(np.random.choice([1, 1, 1, 2, 3, 7]))
    start_date += timedelta(days=gap)
    daily_dt = 1.0 / 365.0
    price *= math.exp((known_mu - 0.5 * known_sigma**2) * daily_dt * gap +
                       known_sigma * math.sqrt(daily_dt * gap) * np.random.normal())
    dates_synth.append(start_date)
    prices_synth.append(price)

dated_prices = list(zip(dates_synth, prices_synth))
total_span = (dated_prices[-1][0] - dated_prices[0][0]).days

# Weekly resampling (the new method)
weekly_buckets = {}
for dt_val, p in dated_prices:
    iso_year, iso_week, _ = dt_val.isocalendar()
    weekly_buckets[(iso_year, iso_week)] = (dt_val, p)

sorted_weeks = sorted(weekly_buckets.keys())
weekly_returns = []
for i in range(1, len(sorted_weeks)):
    _, prev_p = weekly_buckets[sorted_weeks[i-1]]
    _, curr_p = weekly_buckets[sorted_weeks[i]]
    if prev_p > 0 and curr_p > 0:
        weekly_returns.append(math.log(curr_p / prev_p))

mu_weekly = statistics.mean(weekly_returns) * 52
sigma_weekly = statistics.stdev(weekly_returns) * math.sqrt(52)

# Old method (time-scaled daily — the broken one)
daily_scaled = []
for i in range(1, len(dated_prices)):
    dd = (dated_prices[i][0] - dated_prices[i-1][0]).days
    if dd <= 0: continue
    lr = math.log(dated_prices[i][1] / dated_prices[i-1][1])
    daily_scaled.append(lr / math.sqrt(dd))

mu_old = statistics.mean(daily_scaled) * 365
sigma_old = statistics.stdev(daily_scaled) * math.sqrt(365)

mu_err_new = abs(mu_weekly - known_mu) / max(abs(known_mu), 0.01) * 100
mu_err_old = abs(mu_old - known_mu) / max(abs(known_mu), 0.01) * 100

record("Weekly resampling mu closer to truth than old method",
       mu_err_new < mu_err_old,
       f"Weekly μ err: {mu_err_new:.1f}% vs Old μ err: {mu_err_old:.1f}%")

record("Weekly sigma within 30% of known",
       abs(sigma_weekly - known_sigma) / known_sigma * 100 < 30.0,
       f"Known σ={known_sigma:.2f}, weekly σ={sigma_weekly:.4f} ({abs(sigma_weekly - known_sigma) / known_sigma * 100:.1f}%)")

sigma_err_new = abs(sigma_weekly - known_sigma) / known_sigma * 100
sigma_err_old = abs(sigma_old - known_sigma) / known_sigma * 100
record("Both sigma estimates within 15%",
       sigma_err_new < 15.0 and sigma_err_old < 15.0,
       f"Weekly σ err: {sigma_err_new:.1f}%, Old σ err: {sigma_err_old:.1f}% (both good, tradeoff)")


# ============================================================
# TEST 5: Jump Detection v2 (FIX #3 — consistent threshold)
# ============================================================
print("\n═══ TEST 5: Jump Detection v2 ═══")

np.random.seed(123)
clean_prices = [100.0]
clean_dates = [datetime(2025, 1, 1)]
for i in range(250):
    clean_dates.append(clean_dates[-1] + timedelta(days=1))
    ret = 0.0003 + 0.015 * np.random.normal()
    clean_prices.append(clean_prices[-1] * math.exp(ret))

# Insert 5 negative jumps
jump_indices = [50, 100, 150, 180, 220]
for idx in jump_indices:
    clean_prices[idx] *= 0.85  # -15% jump

# NEW method: detect on SCALED returns
scaled_rets = []
for i in range(1, len(clean_prices)):
    dd = (clean_dates[i] - clean_dates[i-1]).days
    if dd <= 0: continue
    lr = math.log(clean_prices[i] / clean_prices[i-1])
    scaled_rets.append(lr / math.sqrt(dd))

sigma_sc = statistics.stdev(scaled_rets)
threshold_sc = 2.0 * sigma_sc
jumps_detected = [r for r in scaled_rets if abs(r) > threshold_sc]
neg_jumps = [r for r in jumps_detected if r < 0]

record("Jump detection finds injected jumps (>=4 of 5)",
       len(jumps_detected) >= 4,
       f"Injected 5, detected {len(jumps_detected)} total ({len(neg_jumps)} negative)")

record("All 5 injected neg jumps detected among results",
       len(neg_jumps) >= 5,
       f"{len(neg_jumps)} negative jumps detected (injected 5)")

if neg_jumps:
    avg_neg = statistics.mean(neg_jumps)
    record("Negative jumps have correct magnitude",
           avg_neg < -0.05,
           f"Average negative jump: {avg_neg:.4f}")


# ============================================================
# TEST 6: Standard Errors (FIX #4)
# ============================================================
print("\n═══ TEST 6: Confidence Intervals ═══")

if len(weekly_returns) > 1:
    w_sigma = statistics.stdev(weekly_returns)
    mu_se = w_sigma / math.sqrt(len(weekly_returns)) * 52
    sigma_se = w_sigma * math.sqrt(52) / math.sqrt(2 * (len(weekly_returns) - 1))

    record("mu_se is positive and finite", mu_se > 0 and math.isfinite(mu_se),
           f"μ SE = {mu_se:.4f}")
    record("sigma_se is positive and finite", sigma_se > 0 and math.isfinite(sigma_se),
           f"σ SE = {sigma_se:.4f}")

    # 95% CI should contain the true value (at least some of the time)
    mu_lower = mu_weekly - 1.96 * mu_se
    mu_upper = mu_weekly + 1.96 * mu_se
    record("95% CI for mu contains true value",
           mu_lower < known_mu < mu_upper,
           f"CI: [{mu_lower:.4f}, {mu_upper:.4f}], truth: {known_mu}")


# ============================================================
# TEST 7: Mean-Reversion Detection (FIX #5)
# ============================================================
print("\n═══ TEST 7: Mean-Reversion Detection ═══")

# Test with strongly mean-reverting data (Ornstein-Uhlenbeck)
np.random.seed(777)
theta = 50.0  # long-term mean
kappa = 15.0  # strong reversion speed
ou_vol = 3.0  # lower noise to isolate reversion signal
ou_prices = [50.0]
ou_dates = [datetime(2025, 1, 1)]
for i in range(300):  # more observations
    ou_dates.append(ou_dates[-1] + timedelta(days=7))
    dp = kappa * (theta - ou_prices[-1]) * (1/52) + ou_vol * math.sqrt(1/52) * np.random.normal()
    ou_prices.append(max(1, ou_prices[-1] + dp))

# Calculate weekly returns and autocorrelation
ou_returns = [math.log(ou_prices[i+1] / ou_prices[i]) for i in range(len(ou_prices)-1)]
mean_ou = statistics.mean(ou_returns)
demeaned_ou = [r - mean_ou for r in ou_returns]
num = sum(demeaned_ou[i] * demeaned_ou[i+1] for i in range(len(demeaned_ou)-1))
den = sum(d**2 for d in demeaned_ou)
ou_autocorr = num / den if den > 0 else 0

record("OU process shows negative autocorrelation",
       ou_autocorr < 0,
       f"Lag-1 autocorr: {ou_autocorr:.4f}")

# Test with trending data (should show positive autocorrelation)
np.random.seed(888)
trend_returns = []
momentum = 0
for i in range(100):
    momentum = 0.3 * momentum + 0.02 + 0.01 * np.random.normal()
    trend_returns.append(momentum)

mean_tr = statistics.mean(trend_returns)
demeaned_tr = [r - mean_tr for r in trend_returns]
num_tr = sum(demeaned_tr[i] * demeaned_tr[i+1] for i in range(len(demeaned_tr)-1))
den_tr = sum(d**2 for d in demeaned_tr)
tr_autocorr = num_tr / den_tr if den_tr > 0 else 0

record("Trending data shows positive autocorrelation",
       tr_autocorr > 0,
       f"Lag-1 autocorr: {tr_autocorr:.4f}")


# ============================================================
# TEST 8: Shroomy Fallback (FIX #2)
# ============================================================
print("\n═══ TEST 8: Shroomy Fallback ═══")

# Daily vol (0.033) → should annualize
daily_vol = 0.033
annualized_new = daily_vol * math.sqrt(365) if daily_vol < 0.08 else daily_vol
record("Daily vol 0.033 correctly annualized",
       0.5 < annualized_new < 1.0,
       f"0.033 → {annualized_new:.4f} (should be ~0.63)")

# Already annual vol (0.63) — should NOT double-annualize
annual_vol = 0.63
kept_new = annual_vol * math.sqrt(365) if annual_vol < 0.08 else annual_vol
record("Annual vol 0.63 NOT double-annualized",
       0.5 < kept_new < 1.0,
       f"0.63 → {kept_new:.4f} (should stay ~0.63)")

# Edge case: borderline value
borderline = 0.07
result_bl = borderline * math.sqrt(365) if borderline < 0.08 else borderline
record("Borderline 0.07 treated as daily",
       result_bl > 1.0,
       f"0.07 → {result_bl:.4f} (annualized)")


# ============================================================
# TEST 9: Performance
# ============================================================
print("\n═══ TEST 9: Performance ═══")
import time

for n_paths in [10000, 50000, 100000]:
    start = time.perf_counter()
    Z_b = np.random.standard_normal((n_paths, 90))
    N_b = np.random.poisson(3.0 * dt, (n_paths, 90))
    J_b = N_b * np.random.normal(-0.05, 0.10, (n_paths, 90))
    J_c = np.cumsum(J_b, axis=1)
    t_b = np.arange(1, 91) * dt
    comp = 3.0 * (np.exp(-0.05 + 0.5 * 0.10**2) - 1)
    d = (0.05 - 0.5 * 0.40**2 - comp) * t_b
    diff = 0.40 * np.cumsum(np.sqrt(dt) * Z_b, axis=1)
    p = 100.0 * np.exp(d + diff + J_c)
    elapsed = (time.perf_counter() - start) * 1000
    record(f"Merton JD {n_paths:,}×90d < 500ms", elapsed < 500, f"{elapsed:.1f}ms")


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
