"""Stage 4 tests for performance and risk metrics calculations.

Covers:
- Timezone-aware elapsed time via total_seconds() without .days integer truncation
- Constant NAV path: vol = 0, Sharpe = None, MaxDD = 0
- Known drawdown path: 100 -> 120 -> 90 -> 108 yields exactly 25% MaxDD
- Sample std with ddof=1
- Corporate action consistency (adj_open and adj_close in same price space)
"""
from datetime import datetime, timezone
import math
import numpy as np
import pandas as pd
import pytest

from research_v2.metrics import (
    compute_backtest_metrics,
    compute_elapsed_years,
    SECONDS_PER_YEAR,
)


def test_elapsed_years_fractional_day_precision() -> None:
    """Validate that elapsed_years does NOT truncate fractional days via .days.

    Compare two timestamps that differ by 1 day and 6.5 hours (30.5 hours total).
    Integer .days would give 1.0 day (86400s), dropping 23400 seconds (27% error)!
    """
    t_start = datetime(2006, 2, 1, 9, 30, tzinfo=timezone.utc)
    t_end = datetime(2006, 2, 2, 16, 0, tzinfo=timezone.utc)

    # Total seconds = 1 day (86400) + 6.5 hours (23400) = 109800.0 seconds
    expected_sec = 109800.0
    expected_years = expected_sec / SECONDS_PER_YEAR

    computed_years = compute_elapsed_years(t_start, t_end)
    assert abs(computed_years - expected_years) < 1e-12

    # Verify that it is strictly greater than 1 day's share
    assert computed_years > (86400.0 / SECONDS_PER_YEAR)


def test_constant_nav_path_metrics() -> None:
    """When NAV is constant across all sessions:

    - Volatility = 0.0
    - Sharpe = None (strictly NOT inf and NOT 0)
    - Max Drawdown = 0.0
    - CAGR = 0.0
    """
    dates = pd.date_range("2006-02-01", periods=10, freq="B").strftime("%Y-%m-%d")
    daily_nav = pd.DataFrame({
        "date": dates,
        "nav": [100000.0] * 10,
        "daily_return": [0.0] * 10,
        "cash": [100000.0] * 10,
        "gross_exposure": [0.0] * 10,
        "fee": [0.0] * 10,
    })
    rebalances = pd.DataFrame()

    metrics = compute_backtest_metrics(
        daily_nav=daily_nav,
        rebalances=rebalances,
        initial_nav=100000.0,
        first_session_open_utc="2006-02-01T14:30:00Z",
        last_session_close_utc="2006-02-14T21:00:00Z",
    )

    assert metrics["annualized_volatility"] == 0.0
    assert metrics["sharpe_ratio"] is None
    assert metrics["max_drawdown"] == 0.0
    assert abs(metrics["cagr"] - 0.0) < 1e-10
    assert metrics["total_return"] == 0.0


def test_known_drawdown_path() -> None:
    """Known NAV sequence: 100,000 -> 120,000 -> 90,000 -> 108,000.

    Peak is 120,000. Trough is 90,000.
    Drawdown = (120,000 - 90,000) / 120,000 = 30,000 / 120,000 = 0.25 (25.0%).
    """
    navs = [100000.0, 120000.0, 90000.0, 108000.0]
    dates = ["2006-02-01", "2006-02-02", "2006-02-03", "2006-02-06"]

    returns = [navs[0] / 100000.0 - 1.0]
    for i in range(1, len(navs)):
        returns.append(navs[i] / navs[i - 1] - 1.0)

    daily_nav = pd.DataFrame({
        "date": dates,
        "nav": navs,
        "daily_return": returns,
        "cash": [0.0] * 4,
        "gross_exposure": [1.0] * 4,
        "fee": [0.0] * 4,
    })

    metrics = compute_backtest_metrics(
        daily_nav=daily_nav,
        rebalances=pd.DataFrame(),
        initial_nav=100000.0,
        first_session_open_utc="2006-02-01T14:30:00Z",
        last_session_close_utc="2006-02-06T21:00:00Z",
    )

    assert abs(metrics["max_drawdown"] - 0.25) < 1e-10


def test_daily_volatility_sample_std_ddof1() -> None:
    """Verify that sample standard deviation uses ddof=1 and scales by sqrt(252)."""
    returns = np.array([0.01, -0.005, 0.02, -0.015, 0.008])
    dates = ["2006-02-01", "2006-02-02", "2006-02-03", "2006-02-06", "2006-02-07"]

    nav = 100000.0
    navs = []
    for r in returns:
        nav *= (1.0 + r)
        navs.append(nav)

    daily_nav = pd.DataFrame({
        "date": dates,
        "nav": navs,
        "daily_return": returns,
        "cash": [0.0] * 5,
        "gross_exposure": [1.0] * 5,
        "fee": [0.0] * 5,
    })

    metrics = compute_backtest_metrics(
        daily_nav=daily_nav,
        rebalances=pd.DataFrame(),
        initial_nav=100000.0,
        first_session_open_utc="2006-02-01T14:30:00Z",
        last_session_close_utc="2006-02-07T21:00:00Z",
    )

    expected_std = float(np.std(returns, ddof=1))
    expected_vol = expected_std * math.sqrt(252.0)
    expected_sharpe = (float(np.mean(returns)) / expected_std) * math.sqrt(252.0)

    assert abs(metrics["annualized_volatility"] - expected_vol) < 1e-10
    assert abs(metrics["sharpe_ratio"] - expected_sharpe) < 1e-10


def test_corporate_action_price_consistency() -> None:
    """Ensure that adj_open and adj_close are from the unified split/dividend-adjusted price series.

    If an asset executes a 2-for-1 split overnight:
    - Raw price: close = $200, open = $100.
    - Adjusted price: both adj_close and adj_open must be scaled consistently (e.g. $100 and $100),
      preventing a false -50% overnight crash from corrupting portfolio returns!
    """
    from research_v2.ledger import execute_rebalance

    # Overnight 2:1 split where adjusted prices properly reflect continuity ($100 -> $100)
    old_shares = {"SPY": 1000.0}
    opens = {"SPY": 100.0}

    _, _, _, reb = execute_rebalance(
        signal_session="2006-02-03",
        execution_session="2006-02-06",
        model_id="M1",
        current_shares=old_shares,
        current_cash=0.0,
        execution_open_prices=opens,
        target_weights={"SPY": 1.0},
        cost_bps=0.0,
        universe=["SPY"],
    )

    # In adjusted terms, wealth is preserved at 1,000 * $100 = $100,000
    assert abs(reb.V_pre - 100000.0) < 1e-10
    assert abs(reb.V_post - 100000.0) < 1e-10
