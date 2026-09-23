"""Unit tests for weekly and monthly matched execution schedules."""
from __future__ import annotations

from pathlib import Path
import pytest

from research_v2.calendar import (
    get_trading_calendar,
    market_as_of_after_close,
    parse_as_of,
)
from research_v2.schedule import (
    build_monthly_matched_schedule,
    build_weekly_schedule,
)


def test_weekly_schedule_ordering_and_pending():
    """Verify weekly schedule has signal_close < execution_open and handles pending terminal."""
    # 2 weeks of trading sessions
    # Week 1: 2026-09-08 to 2026-09-11
    # Week 2: 2026-09-14 to 2026-09-18 (terminal)
    sessions = [
        "2026-09-08", "2026-09-09", "2026-09-10", "2026-09-11",
        "2026-09-14", "2026-09-15", "2026-09-16", "2026-09-17", "2026-09-18",
    ]
    as_of = "2026-09-18T16:01:00-04:00"

    events = build_weekly_schedule(sessions, as_of=as_of)
    assert len(events) == 2

    # Week 1: execution is 2026-09-14, which is in sessions -> COMPLETE
    ev1 = events[0]
    assert ev1.signal_session == "2026-09-11"
    assert ev1.execution_session == "2026-09-14"
    assert ev1.status == "COMPLETE"
    assert parse_as_of(ev1.signal_close_ts) < parse_as_of(ev1.execution_open_ts)

    # Week 2: execution is 2026-09-21, which is NOT in sessions -> PENDING
    ev2 = events[1]
    assert ev2.signal_session == "2026-09-18"
    assert ev2.execution_session == "2026-09-21"
    assert ev2.status == "PENDING"
    assert parse_as_of(ev2.signal_close_ts) < parse_as_of(ev2.execution_open_ts)


def test_monthly_matched_schedule_no_midweek_lookahead():
    """Monthly matched cadence must strictly use latest completed weekly signal."""
    # 2026-04-30 is Thursday. Week 2026-04-27 to 2026-05-01 is not finished on 04-30!
    # Latest completed week as of 2026-04-30 close is 2026-04-24 Friday.
    sessions = [
        # April Week 4
        "2026-04-20", "2026-04-21", "2026-04-22", "2026-04-23", "2026-04-24",
        # April Week 5 (partial at month end)
        "2026-04-27", "2026-04-28", "2026-04-29", "2026-04-30",
        # May Week 5 continuation
        "2026-05-01", "2026-05-04",
    ]
    as_of = "2026-05-01T16:01:00-04:00"

    weekly_events = build_weekly_schedule(sessions, as_of=as_of)
    monthly_events = build_monthly_matched_schedule(sessions, as_of=as_of, weekly_events=weekly_events)

    # Find April 2026 monthly event
    m_apr = [m for m in monthly_events if m.period_id == "M_MATCH_2026_04"]
    assert len(m_apr) == 1
    ev = m_apr[0]

    assert ev.signal_session == "2026-04-30"  # Month-end session
    assert ev.execution_session == "2026-05-01"  # Next session
    # Feature cutoff must be the completed week of 2026-04-24 close, NOT 2026-04-30!
    assert "2026-04-24" in ev.feature_cutoff
    assert ev.source_signal_session == "2026-04-24"


def test_train_partition_event_counts_regression():
    """Regression test: Train partition (2006-02-01 to 2015-12-31) event counts after DST fix.

    Expected:
    - Weekly events = 518
    - Monthly matched events = 119
    - Last Weekly: signal_session == '2015-12-31', execution_session == '2016-01-04'
    - Last Monthly: period_id == 'M_MATCH_2015_12', signal_session == '2015-12-31', execution_session == '2016-01-04'
    """
    cal = get_trading_calendar()
    session_dates = [s.strftime("%Y-%m-%d") for s in cal.sessions_in_range("2005-01-03", "2015-12-31")]
    train_start = "2006-02-01"
    train_end = "2015-12-31"

    as_of = market_as_of_after_close(train_end)
    assert as_of == "2015-12-31T16:01:00-05:00"

    all_weekly = build_weekly_schedule(
        [s for s in session_dates if s <= train_end],
        as_of=as_of,
    )
    train_weekly = [w for w in all_weekly if train_start <= w.signal_session <= train_end]

    assert len(train_weekly) == 518, f"Expected 518 weekly events, got {len(train_weekly)}"
    assert train_weekly[0].signal_session == "2006-02-03"
    assert train_weekly[-1].signal_session == "2015-12-31"
    assert train_weekly[-1].execution_session == "2016-01-04"
    assert train_weekly[-1].status == "PENDING"

    all_monthly = build_monthly_matched_schedule(
        [s for s in session_dates if s <= train_end],
        as_of=as_of,
        weekly_events=all_weekly,
    )
    train_monthly = [m for m in all_monthly if train_start <= m.signal_session <= train_end]

    assert len(train_monthly) == 119, f"Expected 119 monthly matched events, got {len(train_monthly)}"
    assert train_monthly[0].signal_session == "2006-02-28"
    assert train_monthly[-1].signal_session == "2015-12-31"
    assert train_monthly[-1].period_id == "M_MATCH_2015_12"
    assert train_monthly[-1].execution_session == "2016-01-04"
    assert train_monthly[-1].status == "PENDING"
