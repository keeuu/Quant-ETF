"""Sharpe-ratio inference: PSR, DSR (Bailey & Lopez de Prado) and a stationary block bootstrap.

All Sharpe ratios inside the PSR/DSR formulas are in per-observation (daily) units; annualised = daily * sqrt(252).

Formulas
--------
PSR (Bailey & Lopez de Prado 2012), probability that the true SR exceeds SR*:
    PSR(SR*) = Phi( (SR - SR*) * sqrt(T - 1) / sqrt(1 - g3*SR + ((g4 - 1)/4) * SR^2) )
  with SR the sample Sharpe (mean/std, ddof=1), T the number of observations, g3 the skewness and g4 the RAW
  (non-excess, normal = 3) kurtosis of the returns (biased moment estimators).

Expected maximum SR among N independent trials with cross-sectional variance V of SR estimates:
    SR0 = sqrt(V) * ( (1 - gamma) * Phi^-1(1 - 1/N) + gamma * Phi^-1(1 - 1/(N e)) ),  gamma = Euler-Mascheroni

DSR (Bailey & Lopez de Prado 2014) = PSR with SR* = SR0.

Stationary bootstrap (Politis & Romano 1994): blocks of geometric length (mean L); index i+1 follows index i
with probability 1 - 1/L, otherwise a fresh uniform start; wrap-around at the end.
"""
from __future__ import annotations

import math
from statistics import NormalDist

import numpy as np

EULER_GAMMA = 0.5772156649015329
_N = NormalDist()
ANN = 252.0


def sample_moments(r) -> tuple[float, float, float, int]:
    """(daily Sharpe mean/std ddof=1, skewness, raw kurtosis, T). Skew/kurt use biased (1/T) moments."""
    x = np.asarray(r, dtype=float)
    t = len(x)
    sd = x.std(ddof=1)
    sr = float(x.mean() / sd) if sd > 1e-12 else float("nan")
    c = x - x.mean()
    m2 = float(np.mean(c ** 2))
    if m2 <= 1e-24:
        return sr, float("nan"), float("nan"), t
    return sr, float(np.mean(c ** 3) / m2 ** 1.5), float(np.mean(c ** 4) / m2 ** 2), t


def psr_from_moments(sr: float, t: int, skew: float, kurt: float, sr_star: float = 0.0) -> float:
    denom = 1.0 - skew * sr + (kurt - 1.0) / 4.0 * sr ** 2
    if not (denom > 0):
        return float("nan")
    return _N.cdf((sr - sr_star) * math.sqrt(t - 1) / math.sqrt(denom))


def psr(returns, sr_star: float = 0.0) -> float:
    sr, g3, g4, t = sample_moments(returns)
    return psr_from_moments(sr, t, g3, g4, sr_star)


def expected_max_sr(var_sr: float, n_trials: int) -> float:
    """SR0 for N trials; var_sr is the cross-sectional variance of SR estimates (same units as SR)."""
    if n_trials < 2:
        return 0.0
    return math.sqrt(var_sr) * ((1 - EULER_GAMMA) * _N.inv_cdf(1 - 1.0 / n_trials)
                               + EULER_GAMMA * _N.inv_cdf(1 - 1.0 / (n_trials * math.e)))


def dsr(returns, n_trials: int, var_sr_daily: float) -> float:
    return psr(returns, expected_max_sr(var_sr_daily, n_trials))


def annual_to_daily_var(var_annual_sharpe: float) -> float:
    """Variance of annualised Sharpe values -> variance in daily units."""
    return var_annual_sharpe / ANN


def stationary_bootstrap_indices(t: int, mean_block: float, n_resamples: int, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    p = 1.0 / mean_block
    idx = np.empty((n_resamples, t), dtype=np.int64)
    idx[:, 0] = rng.integers(0, t, n_resamples)
    new = rng.random((n_resamples, t)) < p
    starts = rng.integers(0, t, (n_resamples, t))
    for j in range(1, t):
        idx[:, j] = np.where(new[:, j], starts[:, j], (idx[:, j - 1] + 1) % t)
    return idx


def ann_sharpe_rows(x: np.ndarray) -> np.ndarray:
    """Annualised Sharpe of each row of a 2D array (ddof=1)."""
    return x.mean(axis=1) / x.std(axis=1, ddof=1) * math.sqrt(ANN)


def bootstrap_sharpe_diff(a, b, idx: np.ndarray, level: float = 0.90) -> tuple[float, float, float]:
    """(point, lo, hi): annualised SR(a) - SR(b) with percentile CI from paired resampling by idx."""
    a, b = np.asarray(a, float), np.asarray(b, float)
    pt = float(a.mean() / a.std(ddof=1) * math.sqrt(ANN) - b.mean() / b.std(ddof=1) * math.sqrt(ANN))
    d = ann_sharpe_rows(a[idx]) - ann_sharpe_rows(b[idx])
    lo, hi = np.quantile(d, [(1 - level) / 2, 1 - (1 - level) / 2])
    return pt, float(lo), float(hi)
