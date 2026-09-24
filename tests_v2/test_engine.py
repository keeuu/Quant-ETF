"""Stage 4 tests for simulation engine, partition isolation, and schedule gating.

Covers:
- Segment isolation: Train starts with 100% cash $100,000 and 0 shares
- Warmup period does not contribute to PnL, NAV, fees, or turnover
- Boundary schedule handling: signals whose execution_session falls outside the segment are not executed
- Full table output structures: daily_nav, positions, trades, rebalance
"""
import pandas as pd
import pytest

from research_v2.contracts import DatasetManifest, DatasetView, SignalFrame
from research_v2.engine import run_backtest


def test_segment_isolation_initial_cash() -> None:
    """Train partition must start strictly in 100% cash $100,000 without inheriting warmup positions."""
    dates = ["2006-02-01", "2006-02-02", "2006-02-03"]
    df = pd.DataFrame({
        "Date": dates,
        "open": [100.0, 101.0, 102.0],
        "high": [101.0, 102.0, 103.0],
        "low": [99.0, 100.0, 101.0],
        "close": [101.0, 102.0, 103.0],
        "adj_open": [100.0, 101.0, 102.0],
        "adj_close": [101.0, 102.0, 103.0],
        "volume": [1000] * 3,
    })
    bars = {sym: df.copy() for sym in ["SPY", "QQQ", "IWM", "EFA", "EEM", "GLD", "TLT", "IEF"]}

    manifest_stub = DatasetManifest(
        dataset_id="test_snapshot",
        data_sha256="",
        calendar_sha256="",
        protocol_sha256="",
        partitions={"train": {"start": "2006-02-01", "end": "2006-02-03"}},
    )
    dataset_view = DatasetView(
        manifest=manifest_stub,
        partition="train",
        as_of="2026-09-18T16:01:00-04:00",
        feature_start="2005-01-03",
        score_start="2006-02-01",
        score_end="2006-02-03",
        bars=bars,
    )

    # Empty signals (no trades)
    signals = SignalFrame(model_id="W_TEST", rows=[], lineage=[])

    daily_nav, positions, trades, rebalance, pending, metrics = run_backtest(
        dataset_view=dataset_view,
        signals=signals,
        cost_bps=5.0,
        initial_nav=100000.0,
    )

    assert len(daily_nav) == 3
    # Cash must remain exactly $100,000 on day 1
    assert daily_nav.iloc[0]["cash"] == 100000.0
    assert daily_nav.iloc[0]["nav"] == 100000.0
    assert daily_nav.iloc[0]["daily_return"] == 0.0
    assert len(positions) == 0
    assert len(trades) == 0


def test_terminal_schedule_gating() -> None:
    """Signals whose execution_session is outside the partition must NOT be executed."""
    dates = ["2015-12-30", "2015-12-31"]
    df = pd.DataFrame({
        "Date": dates,
        "open": [100.0, 101.0],
        "high": [101.0, 102.0],
        "low": [99.0, 100.0],
        "close": [101.0, 102.0],
        "adj_open": [100.0, 101.0],
        "adj_close": [101.0, 102.0],
        "volume": [1000] * 2,
    })
    bars = {sym: df.copy() for sym in ["SPY", "QQQ", "IWM", "EFA", "EEM", "GLD", "TLT", "IEF"]}

    manifest_stub = DatasetManifest(
        dataset_id="test_snapshot",
        data_sha256="",
        calendar_sha256="",
        protocol_sha256="",
        partitions={"train": {"start": "2015-12-30", "end": "2015-12-31"}},
    )
    dataset_view = DatasetView(
        manifest=manifest_stub,
        partition="train",
        as_of="2026-09-18T16:01:00-04:00",
        feature_start="2005-01-03",
        score_start="2015-12-30",
        score_end="2015-12-31",
        bars=bars,
    )

    # Signal produced on 2015-12-31 with execution on 2016-01-04 (in Validation!)
    signals = SignalFrame(
        model_id="W_TEST",
        rows=[{
            "signal_session": "2015-12-31",
            "execution_session": "2016-01-04",
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

    # The 2016-01-04 execution must NOT be executed within Train partition!
    assert len(trades) == 0
    assert len(rebalance) == 0
    assert daily_nav.iloc[-1]["cash"] == 100000.0

def test_out_of_segment_execution_pending():
    """Test 1: Out-of-segment execution."""
    dates = ["2015-12-28", "2015-12-29", "2015-12-30", "2015-12-31"]
    df = pd.DataFrame({
        "Date": dates,
        "open": [100.0]*4, "high": [101.0]*4, "low": [99.0]*4, "close": [101.0]*4,
        "adj_open": [100.0]*4, "adj_close": [101.0]*4, "volume": [1000]*4,
    })
    bars = {sym: df.copy() for sym in ["SPY", "QQQ", "IWM", "EFA", "EEM", "GLD", "TLT", "IEF"]}

    manifest_stub = DatasetManifest(
        dataset_id="test_snapshot", data_sha256="", calendar_sha256="", protocol_sha256="",
        partitions={"train": {"start": "2015-12-28", "end": "2015-12-31"}},
    )
    dataset_view = DatasetView(
        manifest=manifest_stub, partition="train", as_of="2026-09-18T16:01:00-04:00",
        feature_start="2005-01-03", score_start="2015-12-28", score_end="2015-12-31", bars=bars,
    )

    signals = SignalFrame(
        model_id="W_TEST",
        rows=[{
            "signal_session": "2015-12-31",
            "execution_session": "2016-01-04",
            "weights": {"SPY": 1.0},
            "status": "COMPLETE",
        }],
        lineage=[],
    )

    daily_nav, positions, trades, rebalance, pending, metrics = run_backtest(
        dataset_view=dataset_view, signals=signals, cost_bps=5.0, initial_nav=100000.0,
    )

    assert len(pending) == 1
    assert pending.iloc[0]["status"] == "PENDING_OUTSIDE_SEGMENT"
    assert pending.iloc[0]["reason"] == "EXECUTION_SESSION_AFTER_SEGMENT_END"
    assert len(rebalance) == 0
    assert len(trades) == 0
    assert metrics["total_fees"] == 0.0

def test_in_segment_execution():
    """Test 2: In-segment execution."""
    dates = ["2015-12-28", "2015-12-29", "2015-12-30", "2015-12-31"]
    df = pd.DataFrame({
        "Date": dates,
        "open": [100.0]*4, "high": [101.0]*4, "low": [99.0]*4, "close": [101.0]*4,
        "adj_open": [100.0]*4, "adj_close": [101.0]*4, "volume": [1000]*4,
    })
    bars = {sym: df.copy() for sym in ["SPY", "QQQ", "IWM", "EFA", "EEM", "GLD", "TLT", "IEF"]}

    manifest_stub = DatasetManifest(
        dataset_id="test_snapshot", data_sha256="", calendar_sha256="", protocol_sha256="",
        partitions={"train": {"start": "2015-12-28", "end": "2015-12-31"}},
    )
    dataset_view = DatasetView(
        manifest=manifest_stub, partition="train", as_of="2026-09-18T16:01:00-04:00",
        feature_start="2005-01-03", score_start="2015-12-28", score_end="2015-12-31", bars=bars,
    )

    signals = SignalFrame(
        model_id="W_TEST",
        rows=[{
            "signal_session": "2015-12-30",
            "execution_session": "2015-12-31",
            "weights": {"SPY": 1.0},
            "status": "COMPLETE",
        }],
        lineage=[],
    )

    daily_nav, positions, trades, rebalance, pending, metrics = run_backtest(
        dataset_view=dataset_view, signals=signals, cost_bps=5.0, initial_nav=100000.0,
    )

    assert len(pending) == 0
    assert len(rebalance) == 1
    assert len(trades) == 1

def test_multiple_pending_execution():
    """Test 3: Multiple pending execution."""
    dates = ["2015-12-28", "2015-12-29", "2015-12-30", "2015-12-31"]
    df = pd.DataFrame({
        "Date": dates,
        "open": [100.0]*4, "high": [101.0]*4, "low": [99.0]*4, "close": [101.0]*4,
        "adj_open": [100.0]*4, "adj_close": [101.0]*4, "volume": [1000]*4,
    })
    bars = {sym: df.copy() for sym in ["SPY", "QQQ", "IWM", "EFA", "EEM", "GLD", "TLT", "IEF"]}

    manifest_stub = DatasetManifest(
        dataset_id="test_snapshot", data_sha256="", calendar_sha256="", protocol_sha256="",
        partitions={"train": {"start": "2015-12-28", "end": "2015-12-31"}},
    )
    dataset_view = DatasetView(
        manifest=manifest_stub, partition="train", as_of="2026-09-18T16:01:00-04:00",
        feature_start="2005-01-03", score_start="2015-12-28", score_end="2015-12-31", bars=bars,
    )

    signals = SignalFrame(
        model_id="W_TEST",
        rows=[{
            "signal_session": "2015-12-31",
            "execution_session": "2016-01-04",
            "weights": {"SPY": 1.0},
            "status": "COMPLETE",
        },
        {
            "signal_session": "2016-01-04",
            "execution_session": "2016-01-08",
            "weights": {"SPY": 1.0},
            "status": "COMPLETE",
        }],
        lineage=[],
    )

    daily_nav, positions, trades, rebalance, pending, metrics = run_backtest(
        dataset_view=dataset_view, signals=signals, cost_bps=5.0, initial_nav=100000.0,
    )

    assert len(pending) == 2
    assert pending.iloc[0]["status"] == "PENDING_OUTSIDE_SEGMENT"
    assert pending.iloc[1]["status"] == "PENDING_OUTSIDE_SEGMENT"

def test_no_future_price_dependency():
    """Test 4: No future-price dependency."""
    # Data only goes up to Day 5 (2015-12-31). 2016-01-04 is Day 6 and NOT in the dataset.
    dates = ["2015-12-28", "2015-12-29", "2015-12-30", "2015-12-31"]
    df = pd.DataFrame({
        "Date": dates,
        "open": [100.0]*4, "high": [101.0]*4, "low": [99.0]*4, "close": [101.0]*4,
        "adj_open": [100.0]*4, "adj_close": [101.0]*4, "volume": [1000]*4,
    })
    bars = {sym: df.copy() for sym in ["SPY", "QQQ", "IWM", "EFA", "EEM", "GLD", "TLT", "IEF"]}

    manifest_stub = DatasetManifest(
        dataset_id="test_snapshot", data_sha256="", calendar_sha256="", protocol_sha256="",
        partitions={"train": {"start": "2015-12-28", "end": "2015-12-31"}},
    )
    dataset_view = DatasetView(
        manifest=manifest_stub, partition="train", as_of="2026-09-18T16:01:00-04:00",
        feature_start="2005-01-03", score_start="2015-12-28", score_end="2015-12-31", bars=bars,
    )

    signals = SignalFrame(
        model_id="W_TEST",
        rows=[{
            "signal_session": "2015-12-31",
            "execution_session": "2016-01-04",
            "weights": {"SPY": 1.0},
            "status": "COMPLETE",
        }],
        lineage=[],
    )

    # Should not raise any Error about missing execution prices for Day 6
    daily_nav, positions, trades, rebalance, pending, metrics = run_backtest(
        dataset_view=dataset_view, signals=signals, cost_bps=5.0, initial_nav=100000.0,
    )
    
    assert len(pending) == 1


def test_pending_status_contract_regression():
    """Verify that only valid upstream schedule statuses (COMPLETE, PENDING) can map to PENDING_OUTSIDE_SEGMENT.
    
    Upstream signals with invalid/error/failed statuses must NOT be silently promoted
    to PENDING_OUTSIDE_SEGMENT merely because execution_session > score_end.
    """
    dates = ["2015-12-28", "2015-12-29", "2015-12-30", "2015-12-31"]
    df = pd.DataFrame({
        "Date": dates,
        "open": [100.0]*4, "high": [101.0]*4, "low": [99.0]*4, "close": [101.0]*4,
        "adj_open": [100.0]*4, "adj_close": [101.0]*4, "volume": [1000]*4,
    })
    bars = {sym: df.copy() for sym in ["SPY", "QQQ", "IWM", "EFA", "EEM", "GLD", "TLT", "IEF"]}

    manifest_stub = DatasetManifest(
        dataset_id="test_snapshot", data_sha256="", calendar_sha256="", protocol_sha256="",
        partitions={"train": {"start": "2015-12-28", "end": "2015-12-31"}},
    )
    dataset_view = DatasetView(
        manifest=manifest_stub, partition="train", as_of="2026-09-18T16:01:00-04:00",
        feature_start="2005-01-03", score_start="2015-12-28", score_end="2015-12-31", bars=bars,
    )

    # 1. Invalid / error statuses outside segment must be rejected from pending_records
    invalid_statuses = ["INVALID", "ERROR", "FAILED", "REJECTED", "CORRUPTED"]
    invalid_rows = [
        {
            "signal_session": "2015-12-31",
            "execution_session": "2016-01-04",
            "weights": {"SPY": 1.0},
            "status": st,
        }
        for st in invalid_statuses
    ]
    signals_invalid = SignalFrame(model_id="W_TEST", rows=invalid_rows, lineage=[])

    _, _, _, _, pending_invalid, _ = run_backtest(
        dataset_view=dataset_view, signals=signals_invalid, cost_bps=5.0, initial_nav=100000.0,
    )
    assert len(pending_invalid) == 0, f"Invalid upstream statuses were erroneously marked as pending: {pending_invalid}"

    # 2. Both COMPLETE and PENDING upstream statuses outside segment are valid for PENDING_OUTSIDE_SEGMENT
    valid_rows = [
        {
            "signal_session": "2015-12-31",
            "execution_session": "2016-01-04",
            "weights": {"SPY": 1.0},
            "status": "COMPLETE",
        },
        {
            "signal_session": "2015-12-31",
            "execution_session": "2016-01-05",
            "weights": {"QQQ": 1.0},
            "status": "PENDING",
        },
    ]
    signals_valid = SignalFrame(model_id="W_TEST", rows=valid_rows, lineage=[])

    _, _, _, _, pending_valid, _ = run_backtest(
        dataset_view=dataset_view, signals=signals_valid, cost_bps=5.0, initial_nav=100000.0,
    )
    assert len(pending_valid) == 2
    assert set(pending_valid["status"]) == {"PENDING_OUTSIDE_SEGMENT"}




def test_engine_cash_normalization() -> None:
    """Engine must normalize SignalFrame weights by stripping the pseudo-asset CASH before ledger execution."""
    from research_v2.contracts import DatasetView, SignalFrame
    from research_v2.engine import run_backtest
    import pandas as pd

    dates = ["2018-01-01", "2018-01-02", "2018-01-03"]
    df = pd.DataFrame({
        "Date": dates,
        "adj_close": [100.0, 100.0, 100.0],
        "adj_open": [100.0, 100.0, 100.0],
    })
    bars = {sym: df for sym in ["SPY", "QQQ", "IWM", "EFA", "EEM", "GLD", "TLT", "IEF"]}
    
    class MockManifest: pass
    
    class MockPart:
        start = "2018-01-01"
        end = "2018-01-03"

    ds = DatasetView(
        manifest=MockManifest(),
        partition="test",
        as_of="asof",
        feature_start="2018-01-01",
        score_start="2018-01-01",
        score_end="2018-01-03",
        bars=bars
    )

    # 1. All-cash
    sig_all_cash = SignalFrame(
        model_id="W_TEST",
        rows=[{
            "signal_session": "2018-01-01",
            "execution_session": "2018-01-02",
            "weights": {"SPY": 0.0, "QQQ": 0.0, "CASH": 1.0},
            "status": "COMPLETE",
        }],
        lineage=[],
    )
    nav, pos, tr, reb, pend, met = run_backtest(ds, sig_all_cash, 0.0, 100000.0)
    assert len(reb) == 1
    assert reb.iloc[0]["cash_after"] == 100000.0

    # 2. Partially invested
    sig_partial = SignalFrame(
        model_id="W_TEST",
        rows=[{
            "signal_session": "2018-01-01",
            "execution_session": "2018-01-02",
            "weights": {"SPY": 0.5, "QQQ": 0.0, "CASH": 0.5},
            "status": "COMPLETE",
        }],
        lineage=[],
    )
    nav, pos, tr, reb, pend, met = run_backtest(ds, sig_partial, 0.0, 100000.0)
    assert len(reb) == 1
    assert reb.iloc[0]["cash_after"] == 50000.0
    assert len(tr) == 1
    assert tr.iloc[0]["symbol"] == "SPY"
    assert tr.iloc[0]["new_shares"] == 500.0

    # 3. Fully invested
    sig_full = SignalFrame(
        model_id="W_TEST",
        rows=[{
            "signal_session": "2018-01-01",
            "execution_session": "2018-01-02",
            "weights": {"SPY": 0.5, "QQQ": 0.5, "CASH": 0.0},
            "status": "COMPLETE",
        }],
        lineage=[],
    )
    nav, pos, tr, reb, pend, met = run_backtest(ds, sig_full, 0.0, 100000.0)
    assert len(reb) == 1
    assert reb.iloc[0]["cash_after"] == 0.0
    assert len(tr) == 2

    # 4. No CASH key (legacy/risky-only)
    sig_legacy = SignalFrame(
        model_id="W_TEST",
        rows=[{
            "signal_session": "2018-01-01",
            "execution_session": "2018-01-02",
            "weights": {"SPY": 0.25, "QQQ": 0.25},
            "status": "COMPLETE",
        }],
        lineage=[],
    )
    nav, pos, tr, reb, pend, met = run_backtest(ds, sig_legacy, 0.0, 100000.0)
    assert len(reb) == 1
    assert reb.iloc[0]["cash_after"] == 50000.0
    
