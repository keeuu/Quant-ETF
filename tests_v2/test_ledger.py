"""Stage 4 synthetic hand-calculated tests for self-financing ledger and solver.

Covers:
- Solver cost domain [0, 1) validation and fail-closed bounds
- Test A: All Cash -> 100% A
- Test B: 100% A -> Cash
- Test C: 100% A -> 100% B
- Test D: Three-asset drift rebalance
- Test E: Zero cost (c = 0)
- Test F: No trade (pretrade == target)
- Residual tolerance <= 1e-10 * max(1, V_pre)
- Non-negative cash and no artificial clamping
"""
import math
import pytest

from research_v2.ledger import (
    execute_rebalance,
    brentq_solve,
    LedgerError,
    LedgerSolverError,
    SelfFinancingSolver,
)


def test_solver_cost_domain_constraints() -> None:
    """Solver must strictly require 0 <= cost_rate < 1. Anything else fails closed."""
    a = {"SPY": 100.0}
    w = {"SPY": 1.0}
    v_pre = 100.0

    # Valid bounds
    SelfFinancingSolver.solve(v_pre, a, w, c=0.0)
    SelfFinancingSolver.solve(v_pre, a, w, c=0.0005)
    SelfFinancingSolver.solve(v_pre, a, w, c=0.5)

    # Negative cost
    with pytest.raises(LedgerError, match="must be in"):
        SelfFinancingSolver.solve(v_pre, a, w, c=-0.0001)

    # Cost >= 1.0
    with pytest.raises(LedgerError, match="must be in"):
        SelfFinancingSolver.solve(v_pre, a, w, c=1.0)
    with pytest.raises(LedgerError, match="must be in"):
        SelfFinancingSolver.solve(v_pre, a, w, c=1.5)

    # NaN / Inf
    with pytest.raises(LedgerError, match="must be in"):
        SelfFinancingSolver.solve(v_pre, a, w, c=float("nan"))
    with pytest.raises(LedgerError, match="must be in"):
        SelfFinancingSolver.solve(v_pre, a, w, c=float("inf"))


def test_test_a_all_cash_to_asset() -> None:
    """Test A: 100% Cash -> 100% A.

    V_pre = 100,000, Cash = 100,000, q_old = 0.
    Target: w_A = 1.0, w_cash = 0.
    c = 0.0005 (5 bps).
    Equation: V_post + c * (1.0 * V_post - 0) = V_pre
              V_post * (1 + c) = V_pre
              V_post = 100,000 / 1.0005 = 99,950.0249875...
    fee = c * V_post = 49.975012...
    """
    v_pre = 100000.0
    c = 0.0005
    p_open = 200.0

    current_shares = {"SPY": 0.0}
    target_weights = {"SPY": 1.0}
    opens = {"SPY": p_open}

    new_shares, new_cash, trades, reb = execute_rebalance(
        signal_session="2006-02-03",
        execution_session="2006-02-06",
        model_id="TEST_MODEL",
        current_shares=current_shares,
        current_cash=v_pre,
        execution_open_prices=opens,
        target_weights=target_weights,
        cost_bps=5.0,
        universe=["SPY"],
    )

    expected_v_post = v_pre / (1.0 + c)
    expected_fee = v_pre - expected_v_post

    assert abs(reb.V_pre - v_pre) < 1e-10
    assert abs(reb.V_post - expected_v_post) < 1e-6
    assert abs(reb.fee - expected_fee) < 1e-6
    assert reb.solver_residual <= 1e-10 * v_pre

    # Shares
    assert abs(new_shares["SPY"] - expected_v_post / p_open) < 1e-8
    assert abs(new_cash) < 1e-8

    # Turnover & gross traded ratio
    assert abs(reb.one_way_turnover - 1.0) < 1e-10
    assert abs(reb.gross_traded_ratio - (expected_v_post / v_pre)) < 1e-6
    assert abs(reb.gross_traded_ratio - 1.0) < 0.001


def test_test_b_asset_to_cash() -> None:
    """Test B: 100% A -> 100% Cash.

    Old: q_A = 500, p_open = 200 -> a_A = 100,000. Cash = 0.
    Target: w_A = 0, w_cash = 1.0.
    c = 0.0005 (5 bps).
    Equation: V_post + c * |0 - a_A| = V_pre
              V_post + c * a_A = a_A
              V_post = (1 - c) * a_A = 99,950.0
    fee = c * a_A = 50.0
    """
    p_open = 200.0
    old_shares = 500.0
    c = 0.0005

    current_shares = {"SPY": old_shares}
    target_weights = {"SPY": 0.0}
    opens = {"SPY": p_open}

    new_shares, new_cash, trades, reb = execute_rebalance(
        signal_session="2006-02-03",
        execution_session="2006-02-06",
        model_id="TEST_MODEL",
        current_shares=current_shares,
        current_cash=0.0,
        execution_open_prices=opens,
        target_weights=target_weights,
        cost_bps=5.0,
        universe=["SPY"],
    )

    expected_v_post = (1.0 - c) * 100000.0
    expected_fee = 50.0

    assert abs(reb.V_pre - 100000.0) < 1e-10
    assert abs(reb.V_post - expected_v_post) < 1e-6
    assert abs(reb.fee - expected_fee) < 1e-6
    assert abs(new_shares["SPY"]) < 1e-10
    assert abs(new_cash - expected_v_post) < 1e-6

    # Turnover & gross ratio
    assert abs(reb.one_way_turnover - 1.0) < 1e-10
    assert abs(reb.gross_traded_ratio - 1.0) < 1e-10


def test_test_c_asset_to_asset() -> None:
    """Test C: 100% A -> 100% B full rotation.

    Old: A = 100,000, B = 0, Cash = 0.
    Target: A = 0, B = 1.0, Cash = 0.
    c = 0.0005 (5 bps).
    Equation: V_post + c * (|0 - 100,000| + |1.0 * V_post - 0|) = 100,000
              V_post + c * 100,000 + c * V_post = 100,000
              V_post * (1 + c) = (1 - c) * 100,000
              V_post = 100,000 * (1 - 0.0005) / (1 + 0.0005) = 99,900.0499...
    fee = c * (100,000 + V_post) ≈ 99.95 (costs incurred on both sell A and buy B).
    """
    c = 0.0005
    current_shares = {"SPY": 500.0, "QQQ": 0.0}
    opens = {"SPY": 200.0, "QQQ": 100.0}
    target_weights = {"SPY": 0.0, "QQQ": 1.0}

    new_shares, new_cash, trades, reb = execute_rebalance(
        signal_session="2006-02-03",
        execution_session="2006-02-06",
        model_id="TEST_MODEL",
        current_shares=current_shares,
        current_cash=0.0,
        execution_open_prices=opens,
        target_weights=target_weights,
        cost_bps=5.0,
        universe=["SPY", "QQQ"],
    )

    expected_v_post = 100000.0 * (1.0 - c) / (1.0 + c)
    expected_fee = 100000.0 - expected_v_post

    assert abs(reb.V_pre - 100000.0) < 1e-10
    assert abs(reb.V_post - expected_v_post) < 1e-6
    assert abs(reb.fee - expected_fee) < 1e-6

    # Both sell SPY and buy QQQ are recorded
    assert len(trades) == 2
    spy_trade = next(t for t in trades if t.symbol == "SPY")
    qqq_trade = next(t for t in trades if t.symbol == "QQQ")
    assert spy_trade.trade_notional == -100000.0
    assert abs(qqq_trade.trade_notional - expected_v_post) < 1e-6

    # One-way turnover = 1.0
    # Gross traded ratio ≈ 2.0 (around (100000 + 99900) / 100000 ≈ 1.999)
    assert abs(reb.one_way_turnover - 1.0) < 1e-10
    assert abs(reb.gross_traded_ratio - (100000.0 + expected_v_post) / 100000.0) < 1e-6
    assert 1.99 < reb.gross_traded_ratio < 2.01


def test_test_d_three_asset_drift_rebalance() -> None:
    """Test D: Three assets drift rebalance back to equal weight (1/3 each).

    Pre-trade:
    A: 40,000
    B: 35,000
    C: 25,000
    Cash: 0
    V_pre = 100,000.
    Target: w_A = 1/3, w_B = 1/3, w_C = 1/3.
    """
    opens = {"SPY": 100.0, "QQQ": 100.0, "IWM": 100.0}
    current_shares = {"SPY": 400.0, "QQQ": 350.0, "IWM": 250.0}
    target_weights = {"SPY": 1.0 / 3.0, "QQQ": 1.0 / 3.0, "IWM": 1.0 / 3.0}

    new_shares, new_cash, trades, reb = execute_rebalance(
        signal_session="2006-02-03",
        execution_session="2006-02-06",
        model_id="TEST_MODEL",
        current_shares=current_shares,
        current_cash=0.0,
        execution_open_prices=opens,
        target_weights=target_weights,
        cost_bps=5.0,
        universe=["SPY", "QQQ", "IWM"],
    )

    assert abs(reb.V_pre - 100000.0) < 1e-10
    assert reb.solver_residual <= 1e-10 * 100000.0
    assert reb.fee > 0.0
    assert abs(reb.V_post - (reb.V_pre - reb.fee)) < 1e-8

    # All three assets should have equal weight in V_post
    for sym in ["SPY", "QQQ", "IWM"]:
        val = new_shares[sym] * opens[sym]
        assert abs(val - reb.V_post / 3.0) < 1e-6


def test_test_e_zero_cost() -> None:
    """Test E: Zero Cost (cost_bps = 0.0).

    Must yield exact V_post = V_pre and fee = 0.0 with residual = 0.
    """
    current_shares = {"SPY": 500.0, "QQQ": 0.0}
    opens = {"SPY": 200.0, "QQQ": 100.0}
    target_weights = {"SPY": 0.0, "QQQ": 1.0}

    new_shares, new_cash, trades, reb = execute_rebalance(
        signal_session="2006-02-03",
        execution_session="2006-02-06",
        model_id="TEST_MODEL",
        current_shares=current_shares,
        current_cash=0.0,
        execution_open_prices=opens,
        target_weights=target_weights,
        cost_bps=0.0,
        universe=["SPY", "QQQ"],
    )

    assert reb.V_pre == 100000.0
    assert reb.V_post == 100000.0
    assert reb.fee == 0.0
    assert reb.solver_residual == 0.0
    assert abs(new_shares["QQQ"] * opens["QQQ"] - 100000.0) < 1e-10


def test_test_f_no_trade() -> None:
    """Test F: Pre-trade weights equal target weights.

    Must produce 0 fee, V_post = V_pre, no trades.
    """
    opens = {"SPY": 100.0, "QQQ": 100.0}
    current_shares = {"SPY": 500.0, "QQQ": 500.0}
    target_weights = {"SPY": 0.5, "QQQ": 0.5}

    new_shares, new_cash, trades, reb = execute_rebalance(
        signal_session="2006-02-03",
        execution_session="2006-02-06",
        model_id="TEST_MODEL",
        current_shares=current_shares,
        current_cash=0.0,
        execution_open_prices=opens,
        target_weights=target_weights,
        cost_bps=5.0,
        universe=["SPY", "QQQ"],
    )

    assert reb.V_pre == 100000.0
    assert reb.V_post == 100000.0
    assert reb.fee == 0.0
    assert reb.solver_residual == 0.0
    assert reb.gross_traded_notional == 0.0
    assert reb.gross_traded_ratio == 0.0
    assert reb.one_way_turnover == 0.0
    assert len(trades) == 0
