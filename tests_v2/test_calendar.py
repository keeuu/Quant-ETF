"""Unit and property tests for XNYS calendar, trading sessions, and complete week logic."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
import zoneinfo
import pytest
import pandas as pd

from research_v2.calendar import (
    TIMEZONE_STR,
    build_complete_weeks,
    build_session_table,
    get_calendar_version,
    get_trading_calendar,
    is_early_close,
    is_session,
    market_as_of_after_close,
    next_session,
    parse_as_of,
    previous_session,
)


def test_calendar_version_and_sessions():
    """Verify calendar version and session table construction."""
    ver = get_calendar_version()
    assert ver == "4.13.2"

    df = build_session_table(start="2026-01-02", end="2026-09-18")
    assert not df.empty
    assert "session_date" in df.columns
    assert "open_utc" in df.columns
    assert "close_utc" in df.columns
    assert "is_early_close" in df.columns
    assert df["session_date"].is_monotonic_increasing


def test_good_friday_2026():
    """Good Friday 2026-04-03: week ends Thursday 04-02, next session is Monday 04-06."""
    assert not is_session("2026-04-03"), "Good Friday must be closed"
    assert is_session("2026-04-02"), "Thursday before Good Friday must be open"
    assert is_session("2026-04-06"), "Monday after Good Friday must be open"

    assert next_session("2026-04-02") == "2026-04-06"
    assert previous_session("2026-04-06") == "2026-04-02"

    # Test complete week event generation
    sessions = ["2026-03-30", "2026-03-31", "2026-04-01", "2026-04-02", "2026-04-06"]
    as_of = "2026-04-03T00:00:00-04:00"  # After Thursday close
    events = build_complete_weeks(sessions, as_of=as_of)

    # Find the Good Friday week
    gf_events = [e for e in events if e.signal_session == "2026-04-02"]
    assert len(gf_events) == 1
    event = gf_events[0]
    assert event.signal_session == "2026-04-02"
    assert event.execution_session == "2026-04-06"


def test_labor_day_2026():
    """Labor Day 2026-09-07 (Monday holiday): execution顺延 to Tuesday 2026-09-08."""
    assert not is_session("2026-09-07"), "Labor Day must be closed"
    assert is_session("2026-09-04"), "Friday before Labor Day must be open"
    assert is_session("2026-09-08"), "Tuesday after Labor Day must be open"

    assert next_session("2026-09-04") == "2026-09-08"

    sessions = ["2026-08-31", "2026-09-01", "2026-09-02", "2026-09-03", "2026-09-04", "2026-09-08"]
    as_of = "2026-09-05T12:00:00-04:00"
    events = build_complete_weeks(sessions, as_of=as_of)

    ld_events = [e for e in events if e.signal_session == "2026-09-04"]
    assert len(ld_events) == 1
    assert ld_events[0].execution_session == "2026-09-08"


def test_black_friday_early_close():
    """Black Friday (day after Thanksgiving) has early close at 13:00 NY time."""
    # 2025-11-28 is Black Friday
    assert is_session("2025-11-28")
    assert is_early_close("2025-11-28")

    df = build_session_table(start="2025-11-24", end="2025-11-28")
    bf_row = df[df["session_date"] == "2025-11-28"].iloc[0]
    assert bool(bf_row["is_early_close"]) is True


def test_leap_years_and_dst():
    """Leap days (Feb 29) and daylight saving transitions handled accurately."""
    # Leap days that were trading days
    assert is_session("2008-02-29")
    assert is_session("2012-02-29")
    assert is_session("2016-02-29")
    assert is_session("2024-02-29")

    # DST: Summer EDT (UTC-4), Winter EST (UTC-5)
    cal = get_trading_calendar()
    summer_open = cal.session_open(pd.Timestamp("2026-07-15")).astimezone(zoneinfo.ZoneInfo("UTC"))
    winter_open = cal.session_open(pd.Timestamp("2026-01-15")).astimezone(zoneinfo.ZoneInfo("UTC"))

    # Summer 9:30 AM EDT is 13:30 UTC
    assert summer_open.hour == 13 and summer_open.minute == 30
    # Winter 9:30 AM EST is 14:30 UTC
    assert winter_open.hour == 14 and winter_open.minute == 30


def test_as_of_boundary_sensitivity():
    """Week must not be marked complete if as_of is before final session close."""
    # 2026-09-18 is Friday
    sessions = ["2026-09-14", "2026-09-15", "2026-09-16", "2026-09-17", "2026-09-18"]

    # Before close at 15:59:00 NY time
    as_of_before = "2026-09-18T15:59:00-04:00"
    events_before = build_complete_weeks(sessions, as_of=as_of_before)
    assert len(events_before) == 0, "Should not be complete before final session closes"

    # After close at 16:01:00 NY time
    as_of_after = "2026-09-18T16:01:00-04:00"
    events_after = build_complete_weeks(sessions, as_of=as_of_after)
    assert len(events_after) == 1
    assert events_after[0].signal_session == "2026-09-18"


def test_asset_missing_day_invalidates_week():
    """If an asset is missing any expected session in a week, the week is not complete."""
    sessions = ["2026-09-14", "2026-09-15", "2026-09-16", "2026-09-17", "2026-09-18"]
    as_of = "2026-09-18T16:01:00-04:00"

    # SPY has all sessions, but QQQ is missing Wednesday 2026-09-16
    symbol_dates = {
        "SPY": set(sessions),
        "QQQ": {"2026-09-14", "2026-09-15", "2026-09-17", "2026-09-18"},
    }
    events = build_complete_weeks(sessions, as_of=as_of, symbol_dates=symbol_dates)
    assert len(events) == 0, "Week with missing asset session must be excluded"


def test_market_as_of_after_close_winter():
    """Winter date (2015-12-31) must use EST (UTC-5) and properly recognize session completion."""
    as_of = market_as_of_after_close("2015-12-31")
    assert as_of == "2015-12-31T16:01:00-05:00"
    assert as_of.endswith("-05:00")

    # In week 2015-12-28 to 2016-01-03, 2016-01-01 is New Year's Day holiday.
    # Sessions in that week are 2015-12-28, 29, 30, 31.
    sessions = ["2015-12-28", "2015-12-29", "2015-12-30", "2015-12-31"]
    events = build_complete_weeks(sessions, as_of=as_of)
    assert len(events) == 1
    assert events[0].signal_session == "2015-12-31"
    assert events[0].period_id == "W_2015_W53"

    # Contrast: buggy hardcoded -04:00 evaluates to 20:01:00 UTC, before 21:00:00 UTC close
    as_of_buggy = "2015-12-31T16:01:00-04:00"
    buggy_events = build_complete_weeks(sessions, as_of=as_of_buggy)
    assert len(buggy_events) == 0, "Buggy -04:00 offset incorrectly drops complete week"


def test_market_as_of_after_close_summer():
    """Summer date (2026-09-18) must use EDT (UTC-4)."""
    as_of = market_as_of_after_close("2026-09-18")
    assert as_of == "2026-09-18T16:01:00-04:00"
    assert as_of.endswith("-04:00")


def test_market_as_of_after_close_dst_transitions():
    """DST transitions in Spring and Autumn must automatically adjust offsets."""
    # Spring 2024: clocks forward on Sunday 2024-03-10
    # Friday 2024-03-08 is EST (-05:00)
    as_of_spring_before = market_as_of_after_close("2024-03-08")
    assert as_of_spring_before == "2024-03-08T16:01:00-05:00"
    assert as_of_spring_before.endswith("-05:00")

    # Monday 2024-03-11 is EDT (-04:00)
    as_of_spring_after = market_as_of_after_close("2024-03-11")
    assert as_of_spring_after == "2024-03-11T16:01:00-04:00"
    assert as_of_spring_after.endswith("-04:00")

    # Autumn 2024: clocks back on Sunday 2024-11-03
    # Friday 2024-11-01 is EDT (-04:00)
    as_of_fall_before = market_as_of_after_close("2024-11-01")
    assert as_of_fall_before == "2024-11-01T16:01:00-04:00"
    assert as_of_fall_before.endswith("-04:00")

    # Monday 2024-11-04 is EST (-05:00)
    as_of_fall_after = market_as_of_after_close("2024-11-04")
    assert as_of_fall_after == "2024-11-04T16:01:00-05:00"
    assert as_of_fall_after.endswith("-05:00")


def test_market_as_of_after_close_early_close():
    """Early closes (Black Friday, Day before July 4th) must reflect actual early close time."""
    # Black Friday 2024-11-29 closes at 13:00 NY time (EST = -05:00)
    as_of_bf = market_as_of_after_close("2024-11-29")
    assert as_of_bf == "2024-11-29T13:01:00-05:00"
    assert "13:01:00" in as_of_bf

    # Day before July 4th 2024-07-03 closes at 13:00 NY time (EDT = -04:00)
    as_of_july3 = market_as_of_after_close("2024-07-03")
    assert as_of_july3 == "2024-07-03T13:01:00-04:00"
    assert "13:01:00" in as_of_july3
