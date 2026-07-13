# Conformal Forecasting — SOTA Research & Upgrade Path
_Deep research 2026-07-13 · 108 agents, adversarially verified · for The Undesirables TCG oracle_

## Verdict

No — a regime-aware split-conformal method is a strong, defensible baseline but not unambiguously best-in-class for non-exchangeable, heavy-tailed collectibles markets, because plain split-conformal's guarantees rest on exchangeability, which distribution drift and regime shifts violate; the concrete failure mode is distribution shift (a mean-shift simulation drops coverage from 90% to ~84%; severe image-corruption shift collapses an uncorrected baseline to ~30%), whereas mere stationary temporal dependence costs little (>89% coverage up to AR coefficient 0.99). The highest-leverage, implementable upgrades are (1) online adaptive miscoverage tracking (ACI / AgACI), which restores long-run coverage under drift almost surely regardless of dependence and removes the manual step-size choice via expert aggregation; (2) weighted/NexCP conformal that up-weights recent, distributionally-similar calibration points for provably bounded coverage gap; and (3) Conformalized Quantile Regression (CQR) to make bands adaptive to heteroscedasticity across assets, sharpening conditional coverage that tercile bucketing only coarsely captures. For multi-horizon consistency, recursive multi-step methods (AcMCP) that model serial dependence among horizon errors are the principled replacement for independent per-horizon fits, with a proven finite-sample coverage-error bound that grows with horizon but vanishes as sample size grows. Your existing design (Mondrian regime buckets, calibration-set sizing, VaR quantile inflation) is well-aligned with the evidence on where undercoverage concentrates (high-volatility regimes) and how to correct it, so the recommended path is evolutionary, not a rewrite.

## Verified Findings

### 1. [HIGH] Plain split-conformal's coverage guarantee is broken by distribution shift / regime change (the real TCG failure mode), but is largely preserved under mere stationary temporal dependence.

**Evidence:** Split CP requires exchangeability, violated by distribution drift (Barber et al. 2023). But the failure is specifically distribution SHIFT, not benign dependence: Oliveira et al. (JMLR 2024) prove split CP stays valid under stationary beta-mixing processes (ARMA, GARCH, HMM) up to a small additive penalty that shrinks with calibration-set size, and empirically AR(1) coverage stays >89% up to lambda=0.99, only breaking at lambda>=0.999. The distinct, damaging failure is drift: a mean-shift DGP drops SCP coverage to ~0.84 vs 0.90 nominal because calibrated quantiles are computed from pre-shift data. This directly answers RQ1: coverage loss under moderate dependence is small (~1pp); loss under an actual regime jump is material (~6pp+ and much worse under severe shift).

**Vote:** unanimous (merged 3-0 claims [0],[2],[3],[5],[9],[14],[18]; one 2-1 on [9] headline number)  
**Sources:** https://arxiv.org/abs/2202.13415 · https://www.jmlr.org/papers/volume25/23-1553/23-1553.pdf · https://arxiv.org/pdf/1904.06019 · https://arxiv.org/pdf/2202.07282 · https://arxiv.org/html/2410.13115v2 · https://www.researchgate.net/publication/397712880_A_Gentle_Introduction_to_Conformal_Time_Series_Forecasting

### 2. [HIGH] Adaptive Conformal Inference (ACI) restores long-run target coverage under arbitrary distribution shift by online-updating the miscoverage level; AgACI removes the step-size (gamma) tuning burden via expert aggregation — the single highest-leverage implementable upgrade.

**Evidence:** ACI (Gibbs & Candes 2021) updates alpha_{t+1}=alpha_t+gamma(alpha - 1{y not in C}); the time-averaged miscoverage provably converges to alpha almost surely even without exchangeability, via the deterministic bound |avg_err - alpha| <= (1-2eps)/(gamma*T). Tradeoff: gamma is a genuine knob — on low-dependence data ACI degrades efficiency linearly in gamma and too-large gamma yields unstable/infinite intervals; optimal gamma* depends on dependence strength. AgACI (Zaffran et al. 2022) runs one ACI expert per candidate gamma and aggregates online (BOA), is parameter-free, and is 'more efficient than gamma=0 while maintaining validity.' For a nightly-refit per-asset system this is implementable as a thin online-adjustment layer over the existing offset arrays; AgACI is the recommended variant to avoid manual per-regime gamma tuning.

**Vote:** unanimous (merged 3-0 claims [11],[15],[16],[17])  
**Sources:** https://arxiv.org/pdf/2202.07282 · https://arxiv.org/abs/2106.00170 · https://www.researchgate.net/publication/397712880_A_Gentle_Introduction_to_Conformal_Time_Series_Forecasting

### 3. [HIGH] Weighted / NexCP conformal restores coverage under shift by replacing the empirical quantile with a weighted one (exponential decay or sliding window) that up-weights recent, distributionally-similar calibration points, with a provably bounded coverage gap.

**Evidence:** Barber et al. 2023 replace the empirical quantile with a weighted empirical quantile using fixed weights (w_i proportional to rho^(t_m - t_i), or sliding window) plus a non-symmetric randomization, 'provably robust, with substantially less loss of coverage when exchangeability is violated.' The coverage gap is bounded by a weighted sum of total-variation distances, small when large weights sit on calibration points distributionally similar to the test point. The covariate-shift special case (Tibshirani et al. 2019) reweights by the test/train likelihood ratio to restore distribution-free coverage. Practically simpler than ACI (a static reweight of existing conformity scores, no online state), so it is a low-effort complement or fallback; note robustness depends on the weight/decay choice and unknown TV distances, which ACI adapts to more aggressively.

**Vote:** unanimous (merged 3-0 claims [1],[6],[10]; covariate-shift special case [5],[6])  
**Sources:** https://arxiv.org/abs/2202.13415 · https://arxiv.org/pdf/1904.06019 · https://www.researchgate.net/publication/397712880_A_Gentle_Introduction_to_Conformal_Time_Series_Forecasting

### 4. [HIGH] CQR makes bands adaptive to heteroscedasticity across the input space while retaining the same finite-sample distribution-free MARGINAL guarantee as split conformal — but it does NOT by itself guarantee conditional coverage.

**Evidence:** CQR (Romano, Patterson & Candes, NeurIPS 2019) conformalizes quantile-regression residuals: intervals are 'fully adaptive to heteroscedasticity' (width varies across inputs vs split-conformal's constant/weakly-varying width), and Theorem 1 gives the identical marginal coverage bound [1-alpha, 1-alpha+1/(n+1)]. This directly addresses RQ3: your tercile regime bucketing is a coarse, 3-level approximation of conditional coverage; CQR gives continuous input-adaptivity, which should tighten bands for calm assets and widen them for jumpy ones without a discrete regime boundary. BUT distribution-free conditional coverage is impossible in finite samples (Vovk; Barber et al. limits), so CQR is not a conditional-coverage guarantee — realistic gain is sharper, better-calibrated-per-asset bands, not exact per-asset coverage. Caveat: CQR requires a learned quantile model, a departure from your deterministic-drift point forecast, so it is higher implementation effort than ACI/weighting.

**Vote:** unanimous (merged 3-0 claims [21],[22],[23],[24])  
**Sources:** https://axi.lims.ac.uk/paper/1905.03222 · https://arxiv.org/pdf/1905.03222

### 5. [HIGH] For multi-horizon (1-30 day) bands, recursive multi-step conformal methods that model serial dependence among horizon errors (AcMCP) are the principled replacement for independent per-horizon fits; the finite-sample coverage-error bound grows with horizon h but vanishes as sample size T grows.

**Evidence:** Existing CP methods mostly treat each horizon independently (like your per-horizon offset arrays), ignoring how errors at different horizons relate. AcMCP performs multi-step conformal prediction recursively, explicitly accounting for serial dependence among multi-step forecast errors. Corollary 1 bounds the coverage error by (b+eta*h)/(eta*(T-h)) — increasing in horizon h but converging to zero as T grows. This answers RQ4: your independent per-horizon fit is a reasonable baseline but leaves cross-horizon consistency on the table; the tradeoff is a more complex recursive calibration for band monotonicity/coherence across 1-30 days. Single-source (one primary paper), so confidence in the specific method is high but the SOTA field is thinner here than for ACI/weighting.

**Vote:** unanimous (merged 3-0 claims [19],[20])  
**Sources:** https://arxiv.org/html/2410.13115v2

### 6. [HIGH] EnbPI provides conformal-style intervals without requiring exchangeability and offers asymptotic conditional AND marginal coverage-gap bounds under stationarity/strong-mixing — but it relies on ensemble predictors and a stationarity assumption that heavy-tailed regime shifts can still violate.

**Evidence:** EnbPI (Xu & Xie, ICML 2021 / TPAMI 2023) 'does not require data exchangeability,' wrapping ensemble predictors; Theorem 1 gives a genuinely feature-conditional coverage-gap bound converging to zero under short-term-iid Lipschitz errors + stationary strong-mixing + estimation consistency. Relevant to RQ2/RQ3 but the assumptions are demanding for sparse, heavy-tailed, regime-shifting TCG data, and EnbPI presupposes an ensemble base model — a large architectural change from your deterministic drift point forecast. Recommendation: lower priority than ACI/weighting/CQR for your system precisely because it swaps exchangeability for a stationarity/strong-mixing assumption that your regime shifts break, and requires an ensemble you don't currently have.

**Vote:** unanimous (merged 3-0 claims [12],[13])  
**Sources:** https://arxiv.org/pdf/2010.09107

### 7. [HIGH] The corrective levers the evidence most strongly supports — regime/volatility conditioning plus larger calibration sets — match your existing design, and undercoverage concentrates specifically in high-volatility regimes.

**Evidence:** On real minute-bar financial series (EUR/USD, Brent, S&P500 futures), online split CP holds conditional coverage near 90% across uptrend/downtrend and high/low-vol regimes, and enlarging the calibration set materially closes the gap (EUR/USD high-vol: 87.64% at cal=500 -> 89.85% at cal=5000). High-vol columns are consistently the lowest, confirming undercoverage concentrates in jumpy regimes — validating your calm/medium/jumpy bucketing and your VaR-inflation instinct on the downside. Two caveats the verifiers flagged: this is liquid data with cal sizes up to 5000, unattainable per-asset in sparse thin-daily collectibles (argues for pooling across assets within a regime, i.e. your Mondrian design, rather than per-asset calibration); and the paper's demonstrated lever is calibration SIZE, it does not itself test regime-conditioning as the fix.

**Vote:** unanimous (3-0 claim [4]; corroborated by [2])  
**Sources:** https://www.jmlr.org/papers/volume25/23-1553/23-1553.pdf

### 8. [LOW] For heavy-tailed tail-risk (VaR/CVaR) specifically, the verified evidence base is thin — no confirmed claim established a principled conformal CVaR replacement for your 'inflate the quantile' hack.

**Evidence:** None of the 25 surviving claims addresses RQ5 (calibrated conformal CVaR / tail-risk for heavy-tailed assets or a principled replacement for quantile inflation). The one-sided VaR case is covered implicitly by the weighted/adaptive machinery above (VaR is just an asymmetric conformity score, and ACI/weighting apply directly to it), but the specific question of conformalized CVaR was not answered by any verified claim. This is a genuine gap, not a negative finding — flagged as an open question below.

**Vote:** no confirmed claim on this sub-question  