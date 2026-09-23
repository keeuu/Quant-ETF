"""Critical look-ahead and causal invariance tests for Research V2.

Verifies:
1. Prefix invariance: past signals identical regardless of future truncation.
2. Future poisoning: future price revisions do not leak into past decisions.
3. Execution open poisoning: execution prices cannot leak into signals or weights.
4. One-week skip: modifying skipped week does not change momentum, but does affect trend.
5. Anchor sensitivity: changing true numerator/denominator dates alters momentum.
"""
from __future__ import annotations

import copy
from pathlib import Path
import pandas as pd
import pytest

from research_v2.calendar import market_as_of_after_close
from research_v2.contracts import UNIVERSE
from research_v2.schedule import build_weekly_schedule
from research_v2.signals import compute_model_signals_for_events


def _build_synthetic_dataset(num_weeks: int = 50) -> tuple[dict[str, pd.DataFrame], list[str]]:
    """Build a deterministic, continuous synthetic dataset of multiple complete weeks."""
    # Generate business day sequence (5 sessions per week)
    sessions = pd.date_range("2024-01-08", periods=num_weeks * 5, freq="B").strftime("%Y-%m-%d").tolist()

    bars = {}
    for sym_idx, sym in enumerate(UNIVERSE):
        base_p = 100.0 + sym_idx * 10.0
        rows = []
        for i, s in enumerate(sessions):
            # Deterministic upward trend with small noise
            p = base_p + i * 0.2 + (i % 3) * 0.1
            rows.append({
                "Date": s,
                "open": p - 0.1,
                "high": p + 0.5,
                "low": p - 0.5,
                "close": p,
                "adj_close": p,
                "adj_open": p - 0.1,
                "volume": 100000,
            })
        bars[sym] = pd.DataFrame(rows)
    return bars, sessions


def test_prefix_invariance():
    """Prefix invariance: signals computed on truncated data match full data."""
    bars, sessions = _build_synthetic_dataset(num_weeks=50)
    as_of_full = market_as_of_after_close(sessions[-1])

    weekly_events_full = build_weekly_schedule(sessions, as_of=as_of_full)
    model_full = compute_model_signals_for_events(
        bars=bars,
        session_dates=sessions,
        weekly_events=weekly_events_full,
        model_id="W_M06_T36",
        momentum_months=6,
        trend_weeks=36,
    )

    # Pick a test signal date at week 40
    test_event_idx = 40
    test_event = weekly_events_full[test_event_idx]
    as_of_prefix = test_event.signal_close_ts
    cutoff_date = test_event.signal_session

    # Truncate bars and sessions
    sessions_prefix = [s for s in sessions if s <= cutoff_date]
    bars_prefix = {sym: df[df["Date"] <= cutoff_date].copy() for sym, df in bars.items()}

    weekly_events_prefix = build_weekly_schedule(sessions_prefix, as_of=as_of_prefix)
    model_prefix = compute_model_signals_for_events(
        bars=bars_prefix,
        session_dates=sessions_prefix,
        weekly_events=weekly_events_prefix,
        model_id="W_M06_T36",
        momentum_months=6,
        trend_weeks=36,
    )

    # Verify that the test period signal matches exactly between full and prefix
    full_row = [r for r in model_full.rows if r["period_id"] == test_event.period_id][0]
    prefix_row = [r for r in model_prefix.rows if r["period_id"] == test_event.period_id][0]

    assert full_row["selected_symbols"] == prefix_row["selected_symbols"]
    assert full_row["weights"] == prefix_row["weights"]
    for sym in UNIVERSE:
        assert full_row["momentum_scores"][sym] == prefix_row["momentum_scores"][sym]
        assert full_row["trend_passes"][sym] == prefix_row["trend_passes"][sym]


def test_future_poisoning():
    """Future poisoning: modifying future prices does not alter past decisions."""
    bars, sessions = _build_synthetic_dataset(num_weeks=50)
    as_of = market_as_of_after_close(sessions[-1])
    weekly_events = build_weekly_schedule(sessions, as_of=as_of)

    original_signals = compute_model_signals_for_events(
        bars=bars,
        session_dates=sessions,
        weekly_events=weekly_events,
        model_id="W_M06_T36",
        momentum_months=6,
        trend_weeks=36,
    )

    # Pick test event at index 35
    test_idx = 35
    target_event = weekly_events[test_idx]
    target_session = target_event.signal_session

    # Poison future prices (all sessions after target_session)
    poisoned_bars = copy.deepcopy(bars)
    for sym in UNIVERSE:
        df = poisoned_bars[sym]
        future_mask = df["Date"] > target_session
        # Multiply future prices by 10x
        df.loc[future_mask, "close"] *= 10.0
        df.loc[future_mask, "adj_close"] *= 10.0

    poisoned_signals = compute_model_signals_for_events(
        bars=poisoned_bars,
        session_dates=sessions,
        weekly_events=weekly_events,
        model_id="W_M06_T36",
        momentum_months=6,
        trend_weeks=36,
    )

    # Past decision at target_session must be 100% IDENTICAL
    orig_row = [r for r in original_signals.rows if r["period_id"] == target_event.period_id][0]
    poison_row = [r for r in poisoned_signals.rows if r["period_id"] == target_event.period_id][0]

    assert orig_row["selected_symbols"] == poison_row["selected_symbols"]
    assert orig_row["weights"] == poison_row["weights"]
    for sym in UNIVERSE:
        assert orig_row["momentum_scores"][sym] == poison_row["momentum_scores"][sym]
        assert orig_row["trend_passes"][sym] == poison_row["trend_passes"][sym]


def test_execution_open_poisoning():
    """Execution open poisoning: altering execution open price cannot alter signals or weights."""
    bars, sessions = _build_synthetic_dataset(num_weeks=50)
    as_of = market_as_of_after_close(sessions[-1])
    weekly_events = build_weekly_schedule(sessions, as_of=as_of)

    original_signals = compute_model_signals_for_events(
        bars=bars,
        session_dates=sessions,
        weekly_events=weekly_events,
        model_id="W_M06_T36",
        momentum_months=6,
        trend_weeks=36,
    )

    test_idx = 35
    target_event = weekly_events[test_idx]
    exec_session = target_event.execution_session

    # Poison execution open price dramatically
    poisoned_bars = copy.deepcopy(bars)
    for sym in UNIVERSE:
        df = poisoned_bars[sym]
        exec_mask = df["Date"] == exec_session
        df.loc[exec_mask, "open"] *= 5.0
        df.loc[exec_mask, "adj_open"] *= 5.0

    poisoned_signals = compute_model_signals_for_events(
        bars=poisoned_bars,
        session_dates=sessions,
        weekly_events=weekly_events,
        model_id="W_M06_T36",
        momentum_months=6,
        trend_weeks=36,
    )

    orig_row = [r for r in original_signals.rows if r["period_id"] == target_event.period_id][0]
    poison_row = [r for r in poisoned_signals.rows if r["period_id"] == target_event.period_id][0]

    assert orig_row["selected_symbols"] == poison_row["selected_symbols"]
    assert orig_row["weights"] == poison_row["weights"]


def test_one_week_skip_invariance():
    """One-week skip test: altering skipped week prices leaves momentum invariant, but alters trend."""
    bars, sessions = _build_synthetic_dataset(num_weeks=50)
    as_of = market_as_of_after_close(sessions[-1])
    weekly_events = build_weekly_schedule(sessions, as_of=as_of)

    test_idx = 38
    target_event = weekly_events[test_idx]
    curr_week_session = target_event.signal_session

    original_signals = compute_model_signals_for_events(
        bars=bars,
        session_dates=sessions,
        weekly_events=weekly_events,
        model_id="W_M06_T36",
        momentum_months=6,
        trend_weeks=36,
    )

    # Poison the current completed week close (which must be skipped in momentum numerator!)
    poisoned_bars = copy.deepcopy(bars)
    for sym in UNIVERSE:
        df = poisoned_bars[sym]
        mask = df["Date"] == curr_week_session
        # Drop price of SPY by 50% on current week close
        if sym == "SPY":
            df.loc[mask, "close"] *= 0.5
            df.loc[mask, "adj_close"] *= 0.5

    poisoned_signals = compute_model_signals_for_events(
        bars=poisoned_bars,
        session_dates=sessions,
        weekly_events=weekly_events,
        model_id="W_M06_T36",
        momentum_months=6,
        trend_weeks=36,
    )

    orig_row = [r for r in original_signals.rows if r["period_id"] == target_event.period_id][0]
    poison_row = [r for r in poisoned_signals.rows if r["period_id"] == target_event.period_id][0]

    # 1. Momentum for SPY MUST BE COMPLETELY IDENTICAL (since current week is skipped!)
    assert orig_row["momentum_scores"]["SPY"] == poison_row["momentum_scores"]["SPY"]

    # 2. Trend for SPY MUST CHANGE (since current week close is part of the 36W SMA!)
    orig_lineage = [l for l in original_signals.lineage if l["period_id"] == target_event.period_id][0]
    poison_lineage = [l for l in poisoned_signals.lineage if l["period_id"] == target_event.period_id][0]

    assert orig_lineage["symbols"]["SPY"]["sma_k"] != poison_lineage["symbols"]["SPY"]["sma_k"]


def test_anchor_sensitivity():
    """Altering true numerator or denominator prices strictly changes momentum."""
    bars, sessions = _build_synthetic_dataset(num_weeks=50)
    as_of = market_as_of_after_close(sessions[-1])
    weekly_events = build_weekly_schedule(sessions, as_of=as_of)

    test_idx = 38
    target_event = weekly_events[test_idx]

    original_signals = compute_model_signals_for_events(
        bars=bars,
        session_dates=sessions,
        weekly_events=weekly_events,
        model_id="W_M06_T36",
        momentum_months=6,
        trend_weeks=36,
    )

    orig_lineage = [l for l in original_signals.lineage if l["period_id"] == target_event.period_id][0]
    num_date = orig_lineage["symbols"]["SPY"]["numerator_date"]
    den_date = orig_lineage["symbols"]["SPY"]["denominator_date"]

    # Alter numerator date price
    poisoned_num = copy.deepcopy(bars)
    poisoned_num["SPY"].loc[poisoned_num["SPY"]["Date"] == num_date, "adj_close"] *= 1.1

    signals_num = compute_model_signals_for_events(
        bars=poisoned_num,
        session_dates=sessions,
        weekly_events=weekly_events,
        model_id="W_M06_T36",
        momentum_months=6,
        trend_weeks=36,
    )
    row_num = [r for r in signals_num.rows if r["period_id"] == target_event.period_id][0]
    assert row_num["momentum_scores"]["SPY"] != orig_lineage["symbols"]["SPY"]["momentum"]

    # Alter denominator date price
    poisoned_den = copy.deepcopy(bars)
    poisoned_den["SPY"].loc[poisoned_den["SPY"]["Date"] == den_date, "adj_close"] *= 1.1

    signals_den = compute_model_signals_for_events(
        bars=poisoned_den,
        session_dates=sessions,
        weekly_events=weekly_events,
        model_id="W_M06_T36",
        momentum_months=6,
        trend_weeks=36,
    )
    row_den = [r for r in signals_den.rows if r["period_id"] == target_event.period_id][0]
    assert row_den["momentum_scores"]["SPY"] != orig_lineage["symbols"]["SPY"]["momentum"]
