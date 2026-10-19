"""Metrics, partitions, outcome classification and bootstrap helpers for the us_long study."""
from __future__ import annotations

import math
from typing import Mapping, Sequence

import numpy as np
import pandas as pd

from research_v2.metrics import compute_backtest_metrics
from research_v3.stats import bootstrap_sharpe_diff, psr, stationary_bootstrap_indices
from research_v3.step3 import MIN_YEAR_SESSIONS, calendar_year_returns

SEED, N_RES, BLOCK, LEVEL = 20261019, 2000, 21, 0.90


def daily_rf(dates: Sequence[str], curve) -> np.ndarray:
    return np.array([0.0] + [curve.accrual_factor(dates[i - 1], dates[i]) - 1.0 for i in range(1, len(dates))])


def drawdown_dates(nav: pd.Series, dates: Sequence[str], initial: float):
    """(max_dd, peak_date, trough_date) on the NAV path including the initial capital."""
    v = np.concatenate([[initial], nav.to_numpy(float)])
    d = ["start"] + list(dates)
    run = np.maximum.accumulate(v)
    dd = (run - v) / run
    t = int(dd.argmax())
    p = int(v[: t + 1].argmax())
    return float(dd[t]), d[p], d[t]


def metrics_for(cal, nav: pd.DataFrame, reb: pd.DataFrame, rf: np.ndarray, part: str, start: str, end: str,
                initial_nav: float, year_tbl: pd.DataFrame, pos_w: pd.DataFrame | None = None) -> tuple[np.ndarray, dict]:
    m = nav[(nav["date"] >= start) & (nav["date"] <= end)]
    idx0 = m.index[0]
    prev_nav = initial_nav if idx0 == 0 else float(nav["nav"].iloc[idx0 - 1])
    rb = reb[(reb["execution_session"] >= start) & (reb["execution_session"] <= end)] if len(reb) else reb
    met = compute_backtest_metrics(
        daily_nav=m.reset_index(drop=True), rebalances=rb.reset_index(drop=True), initial_nav=prev_nav,
        first_session_open_utc=cal.session_open(pd.Timestamp(m["date"].iloc[0])),
        last_session_close_utc=cal.session_close(pd.Timestamp(m["date"].iloc[-1])))
    r = m["daily_return"].to_numpy(float)
    ex = r - rf[m.index.to_numpy()]
    sd = ex.std(ddof=1)
    xs = float(ex.mean() / sd * math.sqrt(252.0)) if sd > 1e-12 else float("nan")
    y0, y1 = int(start[:4]), int(end[:4])
    yy = year_tbl[(year_tbl.index >= y0) & (year_tbl.index <= y1) & (year_tbl["sessions"] >= MIN_YEAR_SESSIONS)]["ret"]
    dates = m["date"].tolist()
    dd, pk, tr = drawdown_dates(m["nav"], dates, prev_nav)
    row = {"partition": part, "start": dates[0], "end": dates[-1], "n_sessions": len(m),
           "cagr": met["cagr"], "ann_vol": met["annualized_volatility"],
           "sharpe": met["sharpe_ratio"] if met["sharpe_ratio"] is not None else float("nan"),
           "excess_sharpe": xs, "max_drawdown": met["max_drawdown"], "mdd_peak": pk, "mdd_trough": tr,
           "worst_year": float(yy.min()) if len(yy) else float("nan"),
           "worst_year_which": int(yy.idxmin()) if len(yy) else None,
           "ann_one_way_turnover": met["annualized_one_way_turnover"], "rebalance_count": met["rebalance_count"],
           "mean_cash_weight": met["mean_cash_weight"], "psr0_excess": psr(ex, 0.0), "final_nav": met["final_nav"]}
    if pos_w is not None:
        pw = pos_w.loc[m.index]
        row["mean_bond_weight"] = float(pw["bond_w"].mean())
        row["mean_equity_weight"] = float(pw["equity_w"].mean())
        row["mean_gold_weight"] = float(pw["gold_w"].mean())
    return ex, row


def position_weights(nav: pd.DataFrame, positions: pd.DataFrame, close_prices: Mapping, groups: Mapping,
                     bond_syms=("TLT", "IEF"), eq_syms=("SPY", "QQQ", "IWM", "EFA", "EEM"), gold_syms=("GLD",)):
    out = {"bond_w": np.zeros(len(nav)), "equity_w": np.zeros(len(nav)), "gold_w": np.zeros(len(nav))}
    dates = nav["date"].tolist()
    for k, syms in (("bond_w", bond_syms), ("equity_w", eq_syms), ("gold_w", gold_syms)):
        for s in syms:
            if s not in positions.columns:
                continue
            q = positions[s].to_numpy(float)
            p = np.array([close_prices[s].get(d, 0.0) if q[i] > 1e-10 else 0.0 for i, d in enumerate(dates)])
            out[k] += q * p / nav["nav"].to_numpy(float)
    return pd.DataFrame(out, index=nav.index)


def classify_main(tbl: pd.DataFrame, strat="CAP_DEF_M") -> dict:
    g = lambda part, s, col: float(tbl[(tbl.partition == part) & (tbl.strategy == s)][col].iloc[0])
    c1 = g("FULL", strat, "excess_sharpe") > g("FULL", "US_60_40", "excess_sharpe")
    c2 = g("FULL", strat, "max_drawdown") < g("FULL", "SPY_BH", "max_drawdown")
    c3a = g("SUB_A", strat, "excess_sharpe") > g("SUB_A", "US_60_40", "excess_sharpe")
    c3b = g("SUB_B", strat, "excess_sharpe") > g("SUB_B", "US_60_40", "excess_sharpe")
    if c1 and c2 and c3a and c3b:
        out = "SUPPORTED"
    elif (not c2) and (not c1):
        out = "NOT_SUPPORTED"
    else:
        out = "PARTIAL"
    return {"outcome": out, "C1_full_xsharpe_gt_60_40": bool(c1), "C2_full_mdd_lt_spy_bh": bool(c2),
            "C3a_sub_a_xsharpe_gt_60_40": bool(c3a), "C3b_sub_b_xsharpe_gt_60_40": bool(c3b)}


def classify_challenger(tbl: pd.DataFrame, ch="CRASH_WEEKLY_EXIT", ref="CAP_DEF_M") -> dict:
    g = lambda part, s, col: float(tbl[(tbl.partition == part) & (tbl.strategy == s)][col].iloc[0])
    d_mdd = g("FULL", ref, "max_drawdown") - g("FULL", ch, "max_drawdown")
    d_sh = g("FULL", ch, "excess_sharpe") - g("FULL", ref, "excess_sharpe")
    c1 = d_mdd >= 0.03 - 1e-12
    c2 = d_sh >= -0.05 - 1e-12
    c3a = g("SUB_A", ch, "max_drawdown") <= g("SUB_A", ref, "max_drawdown") + 1e-15
    c3b = g("SUB_B", ch, "max_drawdown") <= g("SUB_B", ref, "max_drawdown") + 1e-15
    return {"outcome": "SUPPORTED" if (c1 and c2 and c3a and c3b) else "NOT_SUPPORTED",
            "C1_full_mdd_lower_by_ge_3pp": bool(c1), "full_mdd_reduction_pp": d_mdd * 100,
            "C2_full_xsharpe_not_lower_by_gt_0.05": bool(c2), "full_xsharpe_change": d_sh,
            "C3a_sub_a_mdd_not_higher": bool(c3a), "C3b_sub_b_mdd_not_higher": bool(c3b)}


def boot_pairs(series: Mapping, part: str, a: str, refs: Sequence[str]) -> list[dict]:
    x = series[(part, a)]
    idx = stationary_bootstrap_indices(len(x), BLOCK, N_RES, SEED)
    rows = []
    for ref in refs:
        pt, lo, hi = bootstrap_sharpe_diff(x, series[(part, ref)], idx, LEVEL)
        rows.append({"partition": part, "strategy": a, "reference": ref, "xsharpe_diff": pt, "ci90_lo": lo,
                     "ci90_hi": hi, "ci_includes_0": bool(lo <= 0 <= hi), "block": BLOCK, "n_resamples": N_RES,
                     "seed": SEED})
    return rows


def partition_bounds(score_start: str, score_end: str, split_a_end: str, split_b_start: str) -> dict:
    return {"FULL": (score_start, score_end), "SUB_A": (score_start, split_a_end), "SUB_B": (split_b_start, score_end)}
