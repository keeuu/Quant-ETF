"""Stage 4 timing tests and daily Mark-to-Market behavior verification.

Covers:
- Overnight Gap timing: previous close -> execution open -> trade
- Intraday Crash: immediate reflection in daily NAV and drawdown
- First-day cost: initial rebalance fee reduces Day 1 return below 0
- Non-rebalance open price decoupling: missing open on non-rebalance day succeeds
- Missing open on rebalance day fails closed
- Missing close on held asset fails closed
"""
import math
import pandas as pd
import pytest

from research_v2.contracts import DatasetManifest, DatasetView, SignalFrame
from research_v2.engine import run_backtest, EngineError
from research_v2.ledger import execute_rebalance, LedgerError


def _make_dummy_dataset_view(bars_dict: dict[str, pd.DataFrame], start: str, end: str) -> DatasetView:
    manifest_stub = DatasetManifest(
        dataset_id="test_snapshot",
        data_sha256="",
        calendar_sha256="",
        protocol_sha256="",
        partitions={"test": {"start": start, "end": end}},
    )
    return DatasetView(
        manifest=manifest_stub,
        partition="test",
        as_of="2026-09-18T16:01:00-04:00",
        feature_start=start,
        score_start=start,
        score_end=end,
        bars=bars_dict,
    )


def test_overnight_gap_timing() -> None:
    """Overnight price jump on an asset held from previous close must be credited to V_pre

    Scenario:
    - Yesterday: Held 1,000 shares of SPY at close = $100 -> NAV = $100,000
    - Today Open: SPY jumps to $110 (+10% overnight gap)
    - Rebalance at Today Open: Sell 100% SPY to 100% Cash
    - V_pre must equal 1,000 * 110 = $110,000 (not $100,000)!
    """
    old_shares = {"SPY": 1000.0}
    opens = {"SPY": 110.0}
    target_weights = {"SPY": 0.0}  # Sell all to cash

    new_shares, new_cash, trades, reb = execute_rebalance(
        signal_session="2006-02-03",
        execution_session="2006-02-06",
        model_id="TEST_MODEL",
        current_shares=old_shares,
        current_cash=0.0,
        execution_open_prices=opens,
        target_weights=target_weights,
        cost_bps=5.0,
        universe=["SPY"],
    )

    # V_pre reflects full overnight gap
    assert abs(reb.V_pre - 110000.0) < 1e-10

    # Fee = 5 bps on $110,000 = $55.0
    expected_fee = 0.0005 * 110000.0
    assert abs(reb.fee - expected_fee) < 1e-6
    assert abs(new_cash - (110000.0 - expected_fee)) < 1e-6


def test_intraday_crash_reflected_in_daily_nav() -> None:
    """Intraday crash on execution day must be immediately captured in close NAV and MaxDD.

    Scenario:
    - Session 1 (2006-02-06): Open at $100, Buy 100% SPY. Close at $70 (-30% crash).
    - NAV at close must drop to ~$70,000.
    """
    # 2-session mini dataset
    dates = ["2006-02-06", "2006-02-07"]
    df_spy = pd.DataFrame({
        "Date": dates,
        "open": [100.0, 70.0],
        "high": [100.0, 72.0],
        "low": [68.0, 69.0],
        "close": [70.0, 71.0],
        "adj_open": [100.0, 70.0],
        "adj_close": [70.0, 71.0],
        "volume": [1000, 1000],
    })
    bars = {"SPY": df_spy}
    for sym in ["QQQ", "IWM", "EFA", "EEM", "GLD", "TLT", "IEF"]:
        bars[sym] = df_spy.copy()

    dataset_view = _make_dummy_dataset_view(bars, "2006-02-06", "2006-02-07")

    signals = SignalFrame(
        model_id="W_TEST",
        rows=[{
            "signal_session": "2006-02-03",
            "execution_session": "2006-02-06",
            "weights": {"SPY": 1.0},
            "status": "COMPLETE",
        }],
        lineage=[],
    )

    daily_nav, positions, trades, rebalance, pending, metrics = run_backtest(
        dataset_view=dataset_view,
        signals=signals,
        cost_bps=5.0,
        initial_nav=100000.0,
    )

    # Day 1 close NAV should reflect ~$70,000 (accounting for 5 bps initial fee)
    day1_nav = daily_nav.iloc[0]["nav"]
    expected_day1_nav = (100000.0 / 1.0005) * 0.7
    assert abs(day1_nav - expected_day1_nav) < 1.0

    # Max Drawdown must capture the ~30% drop
    assert metrics["max_drawdown"] > 0.29


def test_first_day_cost_negative_return() -> None:
    """When capital is deployed on session 1 and intraday price is flat, return must be negative.

    Open = $100, Close = $100.
    100% deployed into SPY at 5 bps cost.
    NAV_close = 100,000 / 1.0005 ≈ 99,950.02.
    First day return = NAV_close / 100,000 - 1 = -0.0004997... < 0!
    """
    dates = ["2006-02-06", "2006-02-07"]
    df_spy = pd.DataFrame({
        "Date": dates,
        "open": [100.0, 100.0],
        "high": [101.0, 101.0],
        "low": [99.0, 99.0],
        "close": [100.0, 100.0],
        "adj_open": [100.0, 100.0],
        "adj_close": [100.0, 100.0],
        "volume": [1000, 1000],
    })
    bars = {sym: df_spy.copy() for sym in ["SPY", "QQQ", "IWM", "EFA", "EEM", "GLD", "TLT", "IEF"]}
    dataset_view = _make_dummy_dataset_view(bars, "2006-02-06", "2006-02-07")

    signals = SignalFrame(
        model_id="W_TEST",
        rows=[{
            "signal_session": "2006-02-03",
            "execution_session": "2006-02-06",
            "weights": {"SPY": 1.0},
            "status": "COMPLETE",
        }],
        lineage=[],
    )

    daily_nav, _, _, _, _, metrics = run_backtest(
        dataset_view=dataset_view,
        signals=signals,
        cost_bps=5.0,
        initial_nav=100000.0,
    )

    day1_ret = daily_nav.iloc[0]["daily_return"]
    assert day1_ret < 0.0
    assert abs(day1_ret - (-0.0005 / 1.0005)) < 1e-6
    assert metrics["first_day_return"] < 0.0


def test_non_rebalance_open_missing_does_not_fail() -> None:
    """Non-rebalance day does NOT require adj_open. Missing adj_open on day 2 must succeed."""
    dates = ["2006-02-06", "2006-02-07"]
    # Day 2 has NaN in adj_open!
    df_spy = pd.DataFrame({
        "Date": dates,
        "open": [100.0, float("nan")],
        "high": [101.0, 102.0],
        "low": [99.0, 99.0],
        "close": [100.0, 105.0],
        "adj_open": [100.0, float("nan")],  # Missing on non-rebalance day
        "adj_close": [100.0, 105.0],        # Valid close
        "volume": [1000, 1000],
    })
    bars = {sym: df_spy.copy() for sym in ["SPY", "QQQ", "IWM", "EFA", "EEM", "GLD", "TLT", "IEF"]}
    dataset_view = _make_dummy_dataset_view(bars, "2006-02-06", "2006-02-07")

    # Only Day 1 is an execution session
    signals = SignalFrame(
        model_id="W_TEST",
        rows=[{
            "signal_session": "2006-02-03",
            "execution_session": "2006-02-06",
            "weights": {"SPY": 1.0},
            "status": "COMPLETE",
        }],
        lineage=[],
    )

    # Must succeed cleanly without failing on day 2 missing open
    daily_nav, _, _, _, _, _ = run_backtest(
        dataset_view=dataset_view,
        signals=signals,
        cost_bps=5.0,
        initial_nav=100000.0,
    )
    assert len(daily_nav) == 2
    assert daily_nav.iloc[1]["nav"] > daily_nav.iloc[0]["nav"]


def test_rebalance_day_open_missing_fails_closed() -> None:
    """Execution day MUST have adj_open. Missing adj_open on rebalance day must raise error."""
    dates = ["2006-02-06", "2006-02-07"]
    df_spy = pd.DataFrame({
        "Date": dates,
        "open": [float("nan"), 100.0],
        "high": [101.0, 101.0],
        "low": [99.0, 99.0],
        "close": [100.0, 100.0],
        "adj_open": [float("nan"), 100.0],  # Missing on execution day 2006-02-06!
        "adj_close": [100.0, 100.0],
        "volume": [1000, 1000],
    })
    bars = {sym: df_spy.copy() for sym in ["SPY", "QQQ", "IWM", "EFA", "EEM", "GLD", "TLT", "IEF"]}
    dataset_view = _make_dummy_dataset_view(bars, "2006-02-06", "2006-02-07")

    signals = SignalFrame(
        model_id="W_TEST",
        rows=[{
            "signal_session": "2006-02-03",
            "execution_session": "2006-02-06",
            "weights": {"SPY": 1.0},
            "status": "COMPLETE",
        }],
        lineage=[],
    )

    with pytest.raises(LedgerError, match="missing or non-positive execution open price"):
        run_backtest(
            dataset_view=dataset_view,
            signals=signals,
            cost_bps=5.0,
            initial_nav=100000.0,
        )


def test_missing_close_on_held_asset_fails_closed() -> None:
    """Missing valuation close price for an asset currently held must fail closed."""
    dates = ["2006-02-06", "2006-02-07"]
    df_spy = pd.DataFrame({
        "Date": dates,
        "open": [100.0, 100.0],
        "high": [101.0, 101.0],
        "low": [99.0, 99.0],
        "close": [100.0, float("nan")],
        "adj_open": [100.0, 100.0],
        "adj_close": [100.0, float("nan")],  # Day 2 close is missing!
        "volume": [1000, 1000],
    })
    bars = {sym: df_spy.copy() for sym in ["SPY", "QQQ", "IWM", "EFA", "EEM", "GLD", "TLT", "IEF"]}
    dataset_view = _make_dummy_dataset_view(bars, "2006-02-06", "2006-02-07")

    signals = SignalFrame(
        model_id="W_TEST",
        rows=[{
            "signal_session": "2006-02-03",
            "execution_session": "2006-02-06",
            "weights": {"SPY": 1.0},
            "status": "COMPLETE",
        }],
        lineage=[],
    )

    with pytest.raises(EngineError, match="missing or invalid valuation close price"):
        run_backtest(
            dataset_view=dataset_view,
            signals=signals,
            cost_bps=5.0,
            initial_nav=100000.0,
        )
