"""Unit tests for weekly momentum and trend filter calculations."""
from __future__ import annotations

import math
from pathlib import Path
import pytest
import pandas as pd

from research_v2.signals import (
    compute_weekly_momentum,
    compute_weekly_trend,
    find_anchor_session,
)


def test_find_anchor_session_natural_months():
    """Verify natural calendar month anchors vs weekly fixed windows."""
    # Mock sessions covering 2023 to 2026
    sessions = pd.date_range("2023-01-01", "2026-09-18", freq="B").strftime("%Y-%m-%d").tolist()

    # 1. 2026-09-18 Friday (leap year + 2 years)
    # 6M: 2026-03-18
    a_6m = find_anchor_session("2026-09-18", 6, sessions)
    assert a_6m == "2026-03-18"

    # 12M: 2025-09-18
    a_12m = find_anchor_session("2026-09-18", 12, sessions)
    assert a_12m == "2025-09-18"

    # 2. Leap year February anchor: 2024-08-31 Saturday -> DateOffset(6M) = 2024-02-29 Thursday (Leap day!)
    # On 2024-08-30 Friday, 6M back is 2024-02-29 Thursday
    a_leap = find_anchor_session("2024-08-30", 6, sessions)
    assert a_leap == "2024-02-29", "Anchor must land on leap day 2024-02-29"

    # 3. Weekend target lands on immediately preceding Friday:
    # 2025-10-19 is Sunday -> anchor <= 2025-10-19 is 2025-10-17 Friday
    a_weekend = find_anchor_session("2026-04-19", 6, sessions)
    assert a_weekend == "2025-10-17"


def test_momentum_hand_calculation():
    """Verify momentum hand calculation and strict 1-week skip."""
    daily_prices = {
        "2026-09-11": 110.0,  # s_(t-1), previous completed week close
        "2026-09-18": 120.0,  # s_t, current completed week close (skipped in numerator)
        "2026-03-18": 100.0,  # a_6M(t), 6-month anchor date
    }

    # Signal on 2026-09-18
    mom, lineage = compute_weekly_momentum(
        signal_session="2026-09-18",
        prev_week_session="2026-09-11",
        anchor_session="2026-03-18",
        daily_adj_close=daily_prices,
    )

    # Momentum = 110.0 / 100.0 - 1.0 = 0.10 (10%)
    # Note that 2026-09-18 price (120.0) is strictly skipped!
    assert mom is not None
    assert math.isclose(mom, 0.10, rel_tol=1e-9)
    assert lineage["numerator_date"] == "2026-09-11"
    assert lineage["numerator_price"] == 110.0
    assert lineage["denominator_date"] == "2026-03-18"
    assert lineage["denominator_price"] == 100.0


def test_trend_filter_strict_operator_and_window():
    """Verify K-week SMA trend filter requires exact K observations and strict '>' operator."""
    # 36-week SMA test
    closes_35 = [100.0] * 35
    # Only 35 observations -> cannot compute
    passed, sma = compute_weekly_trend(100.0, closes_35, window_weeks=36)
    assert passed is False
    assert sma is None

    # 36 observations: 35 of 100.0 and current is 100.0 -> mean = 100.0
    closes_36 = [100.0] * 36
    # Exact equal: 100.0 > 100.0 is False! Must be strict '>'
    passed_eq, sma_eq = compute_weekly_trend(100.0, closes_36, window_weeks=36)
    assert passed_eq is False
    assert sma_eq == 100.0

    # Above SMA: current is 101.0
    closes_36_above = [100.0] * 35 + [101.0]
    passed_above, sma_above = compute_weekly_trend(101.0, closes_36_above, window_weeks=36)
    assert passed_above is True
    assert sma_above is not None and 101.0 > sma_above

    # Below SMA: current is 99.0
    closes_36_below = [100.0] * 35 + [99.0]
    passed_below, sma_below = compute_weekly_trend(99.0, closes_36_below, window_weeks=36)
    assert passed_below is False


def test_monthly_signal_identity():
    """Matched monthly signals must retain exactly the source weekly weights and attributes."""
    from research_v2.contracts import SignalFrame, PeriodEvent
    from research_v2.signals import compute_matched_monthly_signals

    weekly_rows = [
        {
            "period_id": "W_1",
            "signal_session": "2015-10-30",
            "execution_session": "2015-11-02",
            "weights": {"SPY": 1.0, "QQQ": 0.0},
            "status": "COMPLETE",
        },
        {
            "period_id": "W_2",
            "signal_session": "2015-11-27",
            "execution_session": "2015-11-30",
            "weights": {"QQQ": 0.5, "IWM": 0.5},
            "status": "COMPLETE",
        }
    ]
    weekly_lineages = [
        {"signal_session": "2015-10-30", "test": "A"},
        {"signal_session": "2015-11-27", "test": "B"},
    ]
    weekly_signals = SignalFrame(model_id="W_TEST", rows=weekly_rows, lineage=weekly_lineages)

    # A monthly event taking W_2 as source
    monthly_events = [
        PeriodEvent(
            period_id="M_MATCH_2015_11",
            signal_session="2015-11-30",
            signal_close_ts="2015-11-30T16:00:00Z",
            execution_session="2015-12-01",
            execution_open_ts="2015-12-01T09:30:00Z",
            feature_cutoff="2015-11-27T16:00:00Z",
            status="COMPLETE",
            source_signal_session="2015-11-27",
        )
    ]

    monthly_signals = compute_matched_monthly_signals(weekly_signals, monthly_events, "M_TEST")

    assert monthly_signals.model_id == "M_TEST"
    assert len(monthly_signals.rows) == 1
    m_row = monthly_signals.rows[0]
    assert m_row["period_id"] == "M_MATCH_2015_11"
    assert m_row["signal_session"] == "2015-11-30"
    assert m_row["execution_session"] == "2015-12-01"
    assert m_row["status"] == "COMPLETE"
    # Identity verified: must hold exact weights from the source W_2
    assert m_row["weights"] == {"QQQ": 0.5, "IWM": 0.5}

    m_lin = monthly_signals.lineage[0]
    assert m_lin["signal_session"] == "2015-11-30"
    assert m_lin["test"] == "B"
