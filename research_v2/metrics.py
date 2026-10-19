"""Daily portfolio performance and risk metrics for Research V2.

Strictly adheres to protocol conventions:
- Elapsed time calculated from timezone-aware session open to session close via total_seconds() / (365.25 * 86400)
- Daily annualized volatility: sample std (ddof=1) * sqrt(252)
- Daily Sharpe (rf=0): mean(r) / std(r, ddof=1) * sqrt(252); returns None if std == 0
- Daily Max Drawdown: running peak-to-trough from daily NAV sequence (positive magnitude)
- Dual turnover metrics: annualized one-way turnover vs annualized gross traded notional ratio
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any, Mapping
import numpy as np
import pandas as pd

SECONDS_PER_YEAR = 365.25 * 86400.0


def parse_utc_timestamp(ts: Any) -> datetime:
    """Parse string or Timestamp into timezone-aware UTC datetime."""
    if isinstance(ts, datetime):
        if ts.tzinfo is None:
            return ts.replace(tzinfo=timezone.utc)
        return ts.astimezone(timezone.utc)
    dt = pd.to_datetime(ts)
    if dt.tzinfo is None:
        return dt.tz_localize("UTC").to_pydatetime()
    return dt.tz_convert("UTC").to_pydatetime()


def compute_elapsed_years(first_open: Any, last_close: Any) -> float:
    """Compute exact elapsed time in years using total_seconds() without day truncation.

    elapsed_years = (last_close_utc - first_open_utc).total_seconds() / (365.25 * 86400)
    """
    t_start = parse_utc_timestamp(first_open)
    t_end = parse_utc_timestamp(last_close)

    total_sec = (t_end - t_start).total_seconds()
    if total_sec <= 0:
        raise ValueError(f"Non-positive elapsed time: start={t_start}, end={t_end}, seconds={total_sec}")

    return total_sec / SECONDS_PER_YEAR


def compute_backtest_metrics(
    daily_nav: pd.DataFrame,
    rebalances: pd.DataFrame,
    initial_nav: float,
    first_session_open_utc: Any,
    last_session_close_utc: Any,
) -> dict[str, Any]:
    """Compute complete performance, risk, and turnover metrics.

    Parameters:
        daily_nav: DataFrame with columns [date, nav, daily_return, cash, gross_exposure, fee]
        rebalances: DataFrame with columns [one_way_turnover, gross_traded_notional, gross_traded_ratio, fee]
        initial_nav: Starting cash capital (e.g. 100000.0)
        first_session_open_utc: Exact UTC open time of the first scored session
        last_session_close_utc: Exact UTC close time of the last scored session
    """
    if daily_nav.empty:
        raise ValueError("Cannot compute metrics on empty daily_nav")

    # 1. Elapsed time
    elapsed_years = compute_elapsed_years(first_session_open_utc, last_session_close_utc)

    # 2. Wealth & CAGR
    final_nav = float(daily_nav["nav"].iloc[-1])
    first_day_return = float(daily_nav["daily_return"].iloc[0])
    total_return = (final_nav / initial_nav) - 1.0

    if final_nav > 0:
        cagr = (final_nav / initial_nav) ** (1.0 / elapsed_years) - 1.0
    else:
        cagr = -1.0

    # 3. Daily returns, Volatility & Sharpe
    returns = daily_nav["daily_return"].to_numpy(dtype=float)
    n_days = len(returns)

    if n_days >= 2:
        mean_r = float(np.mean(returns))
        std_r = float(np.std(returns, ddof=1))
    else:
        mean_r = float(returns[0]) if n_days == 1 else 0.0
        std_r = 0.0

    if std_r > 1e-12:
        ann_vol = std_r * math.sqrt(252.0)
        sharpe = (mean_r / std_r) * math.sqrt(252.0)
    else:
        ann_vol = 0.0
        sharpe = None

    # 4. Max Drawdown (evaluated on NAV path including initial capital)
    nav_series = pd.concat([pd.Series([initial_nav]), daily_nav["nav"]], ignore_index=True)
    running_max = nav_series.cummax()
    drawdowns = (running_max - nav_series) / running_max
    max_drawdown = float(drawdowns.max())

    # 5. Turnover & Fees
    if not rebalances.empty:
        total_one_way_turnover = float(rebalances["one_way_turnover"].sum())
        total_gross_traded_notional = float(rebalances["gross_traded_notional"].sum())
        sum_gross_trade_ratio = float(rebalances["gross_traded_ratio"].sum())
        total_fees = float(rebalances["fee"].sum())
        rebalance_count = len(rebalances)
    else:
        total_one_way_turnover = 0.0
        total_gross_traded_notional = 0.0
        sum_gross_trade_ratio = 0.0
        total_fees = 0.0
        rebalance_count = 0

    ann_one_way_turnover = total_one_way_turnover / elapsed_years
    ann_gross_traded_ratio = sum_gross_trade_ratio / elapsed_years

    # 6. Cash and Exposure
    cash_weights = daily_nav["cash"] / daily_nav["nav"]
    mean_cash_weight = float(cash_weights.mean())
    max_cash_weight = float(cash_weights.max())
    mean_gross_exposure = float(daily_nav["gross_exposure"].mean())

    return {
        "initial_nav": initial_nav,
        "final_nav": final_nav,
        "total_return": total_return,
        "cagr": cagr,
        "elapsed_years": elapsed_years,
        "trading_days": n_days,
        "first_day_return": first_day_return,
        "annualized_volatility": ann_vol,
        "sharpe_ratio": sharpe,
        "max_drawdown": max_drawdown,
        "rebalance_count": rebalance_count,
        "total_fees": total_fees,
        "total_one_way_turnover": total_one_way_turnover,
        "annualized_one_way_turnover": ann_one_way_turnover,
        "total_gross_traded_notional": total_gross_traded_notional,
        "annualized_gross_traded_ratio": ann_gross_traded_ratio,
        "mean_cash_weight": mean_cash_weight,
        "max_cash_weight": max_cash_weight,
        "mean_gross_exposure": mean_gross_exposure,
    }
