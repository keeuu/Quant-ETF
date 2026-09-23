"""Stage 4 tests for transaction costs and turnover metrics.

Covers:
- Multi-cost stress tests: 0, 5, 10, 20, 30, 50 bps
- Total fees monotonically increase with cost_bps
- Final wealth does not increase with cost_bps
- Clear distinction between cash-inclusive one-way turnover and gross traded notional ratio:
  * 100% A -> 100% B: one-way = 1, gross ratio ≈ 2
  * 100% A -> Cash: one-way = 1, gross ratio ≈ 1
  * Cash -> 100% A: one-way = 1, gross ratio ≈ 1
"""
import pandas as pd
import pytest

from research_v2.contracts import DatasetManifest, DatasetView, SignalFrame
from research_v2.engine import run_backtest
from research_v2.ledger import execute_rebalance


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


def test_turnover_definitions_separation() -> None:
    """Validate that one-way turnover and gross traded ratio behave as specified across cases."""
    opens = {"SPY": 100.0, "QQQ": 100.0}

    # Case 1: Cash -> 100% SPY
    _, _, _, reb_cash_to_a = execute_rebalance(
        signal_session="2006-02-03",
        execution_session="2006-02-06",
        model_id="M1",
        current_shares={"SPY": 0.0, "QQQ": 0.0},
        current_cash=100000.0,
        execution_open_prices=opens,
        target_weights={"SPY": 1.0, "QQQ": 0.0},
        cost_bps=5.0,
        universe=["SPY", "QQQ"],
    )
    assert abs(reb_cash_to_a.one_way_turnover - 1.0) < 1e-10
    assert abs(reb_cash_to_a.gross_traded_ratio - 1.0) < 0.001

    # Case 2: 100% SPY -> Cash
    _, _, _, reb_a_to_cash = execute_rebalance(
        signal_session="2006-02-03",
        execution_session="2006-02-06",
        model_id="M1",
        current_shares={"SPY": 1000.0, "QQQ": 0.0},
        current_cash=0.0,
        execution_open_prices=opens,
        target_weights={"SPY": 0.0, "QQQ": 0.0},
        cost_bps=5.0,
        universe=["SPY", "QQQ"],
    )
    assert abs(reb_a_to_cash.one_way_turnover - 1.0) < 1e-10
    assert abs(reb_a_to_cash.gross_traded_ratio - 1.0) < 1e-10

    # Case 3: 100% SPY -> 100% QQQ (Full Rotation)
    _, _, _, reb_a_to_b = execute_rebalance(
        signal_session="2006-02-03",
        execution_session="2006-02-06",
        model_id="M1",
        current_shares={"SPY": 1000.0, "QQQ": 0.0},
        current_cash=0.0,
        execution_open_prices=opens,
        target_weights={"SPY": 0.0, "QQQ": 1.0},
        cost_bps=5.0,
        universe=["SPY", "QQQ"],
    )
    assert abs(reb_a_to_b.one_way_turnover - 1.0) < 1e-10
    # Gross traded ratio ≈ 2.0, significantly distinct from one-way turnover!
    assert 1.99 < reb_a_to_b.gross_traded_ratio < 2.01


def test_multi_cost_scenarios_monotonicity() -> None:
    """Fixed signal path tested across 0, 5, 10, 20, 30, 50 bps.

    Invariants:
    - total_fees must monotonically increase with cost_bps
    - final_nav must monotonically decrease (or remain non-increasing) with cost_bps
    """
    dates = ["2006-02-06", "2006-02-07", "2006-02-08", "2006-02-09", "2006-02-10"]
    # Prices drift slightly
    df = pd.DataFrame({
        "Date": dates,
        "open": [100.0, 101.0, 102.0, 103.0, 104.0],
        "high": [101.0, 102.0, 103.0, 104.0, 105.0],
        "low": [99.0, 100.0, 101.0, 102.0, 103.0],
        "close": [101.0, 102.0, 103.0, 104.0, 105.0],
        "adj_open": [100.0, 101.0, 102.0, 103.0, 104.0],
        "adj_close": [101.0, 102.0, 103.0, 104.0, 105.0],
        "volume": [1000] * 5,
    })
    bars = {sym: df.copy() for sym in ["SPY", "QQQ", "IWM", "EFA", "EEM", "GLD", "TLT", "IEF"]}
    dataset_view = _make_dummy_dataset_view(bars, dates[0], dates[-1])

    # Rebalance on day 1 (buy SPY) and day 3 (switch to QQQ)
    signals = SignalFrame(
        model_id="W_TEST",
        rows=[
            {
                "signal_session": "2006-02-03",
                "execution_session": "2006-02-06",
                "weights": {"SPY": 1.0, "QQQ": 0.0},
                "status": "COMPLETE",
            },
            {
                "signal_session": "2006-02-07",
                "execution_session": "2006-02-08",
                "weights": {"SPY": 0.0, "QQQ": 1.0},
                "status": "COMPLETE",
            },
        ],
        lineage=[],
    )

    cost_tiers = [0.0, 5.0, 10.0, 20.0, 30.0, 50.0]
    total_fees_list = []
    final_nav_list = []

    for bps in cost_tiers:
        daily_nav, _, _, _, _, metrics = run_backtest(
            dataset_view=dataset_view,
            signals=signals,
            cost_bps=bps,
            initial_nav=100000.0,
        )
        total_fees_list.append(metrics["total_fees"])
        final_nav_list.append(metrics["final_nav"])

    # 1. Total fees must strictly increase with cost_bps (for bps > 0)
    for i in range(1, len(cost_tiers)):
        assert total_fees_list[i] > total_fees_list[i - 1], (
            f"Fees failed to increase from {cost_tiers[i-1]}bps to {cost_tiers[i]}bps: "
            f"{total_fees_list[i-1]} vs {total_fees_list[i]}"
        )

    # 2. Final NAV must strictly decrease with higher costs
    for i in range(1, len(cost_tiers)):
        assert final_nav_list[i] < final_nav_list[i - 1], (
            f"Final NAV failed to decrease with higher costs from {cost_tiers[i-1]}bps to {cost_tiers[i]}bps: "
            f"{final_nav_list[i-1]} vs {final_nav_list[i]}"
        )
