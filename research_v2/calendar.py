"""XNYS trading calendar and complete period builders for Research V2.

Standardizes session dates, open/close timestamps, early closures,
and strict Monday-Sunday natural week boundaries.
"""
from __future__ import annotations

import importlib.metadata
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping, Sequence
import zoneinfo

import exchange_calendars as ecals
import pandas as pd

from .contracts import PeriodEvent, PeriodTable
from .provenance import sha256

TIMEZONE_STR = "America/New_York"
NY_TZ = zoneinfo.ZoneInfo(TIMEZONE_STR)
DEFAULT_CALENDAR_START = "2000-01-01"
DEFAULT_CALENDAR_END = "2030-12-31"


def get_calendar_version() -> str:
    """Return the installed exchange-calendars version."""
    return importlib.metadata.version("exchange-calendars")


@lru_cache(maxsize=1)
def get_trading_calendar(
    start: str = DEFAULT_CALENDAR_START,
    end: str = DEFAULT_CALENDAR_END,
) -> ecals.ExchangeCalendar:
    """Get the XNYS calendar singleton with explicit historical start."""
    return ecals.get_calendar("XNYS", start=start, end=end)


def parse_as_of(as_of: str | datetime) -> datetime:
    """Parse as_of into an explicit timezone-aware datetime in UTC."""
    if isinstance(as_of, datetime):
        dt = as_of
    else:
        # Handles ISO-8601 strings with timezone offset
        dt = datetime.fromisoformat(as_of)
    if dt.tzinfo is None:
        raise ValueError("as_of must have an explicit timezone offset, naive datetime is forbidden")
    return dt.astimezone(zoneinfo.ZoneInfo("UTC"))


def market_as_of_after_close(
    session_date: str | datetime | pd.Timestamp,
    minutes_after_close: int = 1,
) -> str:
    """Construct an America/New_York timezone-aware market as_of timestamp strictly after session close.

    Uses XNYS actual session_close as authority to correctly handle:
    - Winter EST (-05:00) vs Summer EDT (-04:00) transitions
    - Early closes (e.g. 13:00 on Black Friday or Christmas Eve)
    - Exceptional calendar schedules

    Returns:
        ISO 8601 formatted timestamp string with explicit America/New_York timezone offset.
    """
    cal = get_trading_calendar()
    ts = pd.Timestamp(session_date)
    if cal.is_session(ts):
        close_utc = cal.session_close(ts)
        close_ny = close_utc.astimezone(NY_TZ)
        as_of_dt = close_ny + pd.Timedelta(minutes=minutes_after_close)
        return as_of_dt.isoformat()
    else:
        # Non-trading session fallback: interpret date in America/New_York at standard 16:00 + delta
        dt = datetime(ts.year, ts.month, ts.day, 16, minutes_after_close, 0, tzinfo=NY_TZ)
        return dt.isoformat()


def build_session_table(
    start: str = "2005-01-03",
    end: str = "2026-09-18",
) -> pd.DataFrame:
    """Construct deterministic XNYS session schedule table.

    Includes UTC and local open/close timestamps and early close flags.
    """
    cal = get_trading_calendar()
    sessions = cal.sessions_in_range(start, end)

    records = []
    for s in sessions:
        s_date = s.strftime("%Y-%m-%d")
        open_utc = cal.session_open(s)
        close_utc = cal.session_close(s)
        open_ny = open_utc.astimezone(NY_TZ)
        close_ny = close_utc.astimezone(NY_TZ)

        # Early close if closing before standard 16:00 NY time or in calendar early_closes
        is_early = (close_ny.hour < 16) or (s in cal.early_closes)

        iso_cal = s.isocalendar()
        records.append({
            "session_date": s_date,
            "open_utc": open_utc.isoformat(),
            "close_utc": close_utc.isoformat(),
            "open_local": open_ny.strftime("%Y-%m-%d %H:%M:%S%z"),
            "close_local": close_ny.strftime("%Y-%m-%d %H:%M:%S%z"),
            "is_early_close": bool(is_early),
            "day_of_week": int(s.dayofweek),  # Monday=0, Sunday=6
            "iso_year": int(iso_cal.year),
            "iso_week": int(iso_cal.week),
        })

    df = pd.DataFrame(records)
    return df


def is_early_close(date_str: str | pd.Timestamp) -> bool:
    """Check if date_str is an early close session."""
    cal = get_trading_calendar()
    ts = pd.Timestamp(date_str)
    return ts in cal.early_closes


def is_session(date_str: str) -> bool:
    """Check if date_str (YYYY-MM-DD) is an XNYS trading session."""
    cal = get_trading_calendar()
    try:
        ts = pd.Timestamp(date_str)
        return cal.is_session(ts)
    except Exception:
        return False


def next_session(date_str: str) -> str:
    """Return next trading session date (YYYY-MM-DD) after date_str."""
    cal = get_trading_calendar()
    ts = pd.Timestamp(date_str)
    nxt = cal.next_session(ts)
    return nxt.strftime("%Y-%m-%d")


def previous_session(date_str: str) -> str:
    """Return previous trading session date (YYYY-MM-DD) before date_str."""
    cal = get_trading_calendar()
    ts = pd.Timestamp(date_str)
    prev = cal.previous_session(ts)
    return prev.strftime("%Y-%m-%d")


def build_complete_weeks(
    session_dates: Sequence[str],
    as_of: str | datetime,
    symbol_dates: Mapping[str, set[str]] | None = None,
) -> list[PeriodEvent]:
    """Construct Monday-Sunday natural week period schedule.

    A week is COMPLETE only if:
    1. All expected XNYS sessions within the Monday-Sunday window have occurred.
    2. The week's final session close_ts is <= as_of.
    3. If symbol_dates is provided, all assets have data on every expected session.

    When Friday is a holiday, Thursday is the week's signal session.
    When Monday is a holiday, execution takes place on Tuesday open.
    """
    as_of_utc = parse_as_of(as_of)
    cal = get_trading_calendar()

    all_sessions_set = set(session_dates)
    if not all_sessions_set:
        return []

    # Map session dates to pd.Timestamp
    ts_sessions = [pd.Timestamp(s) for s in sorted(all_sessions_set)]
    start_dt = ts_sessions[0]
    end_dt = ts_sessions[-1]

    # Find start of the first Monday and end of the last Sunday
    first_monday = start_dt - pd.Timedelta(days=start_dt.dayofweek)
    last_sunday = end_dt + pd.Timedelta(days=6 - end_dt.dayofweek)

    events: list[PeriodEvent] = []

    current_monday = first_monday
    while current_monday <= last_sunday:
        current_sunday = current_monday + pd.Timedelta(days=6)
        mon_str = current_monday.strftime("%Y-%m-%d")
        sun_str = current_sunday.strftime("%Y-%m-%d")

        # Query all expected XNYS sessions in this Monday-Sunday window
        expected_in_week = [
            s.strftime("%Y-%m-%d")
            for s in cal.sessions_in_range(mon_str, sun_str)
        ]

        if not expected_in_week:
            # Legal full-week holiday (extremely rare)
            current_monday += pd.Timedelta(days=7)
            continue

        # Check if the entire week falls within our snapshot range
        # If the first week in dataset starts midweek, it's a partial week
        if any(s not in all_sessions_set for s in expected_in_week):
            # Midweek start or missing from data
            current_monday += pd.Timedelta(days=7)
            continue

        final_session_str = expected_in_week[-1]
        final_session_ts = pd.Timestamp(final_session_str)
        close_utc = cal.session_close(final_session_ts).astimezone(zoneinfo.ZoneInfo("UTC"))

        # Check as_of: final session must have closed
        if close_utc > as_of_utc:
            # Week is not complete as of the evaluation cutoff
            current_monday += pd.Timedelta(days=7)
            continue

        # Check symbol coverage if provided
        data_missing = False
        if symbol_dates:
            for sym, s_set in symbol_dates.items():
                for s in expected_in_week:
                    if s not in s_set:
                        data_missing = True
                        break
                if data_missing:
                    break

        if data_missing:
            # Not complete due to missing asset data
            current_monday += pd.Timedelta(days=7)
            continue

        # Next trading session for execution
        exec_session_ts = cal.next_session(final_session_ts)
        exec_session_str = exec_session_ts.strftime("%Y-%m-%d")
        open_utc = cal.session_open(exec_session_ts).astimezone(zoneinfo.ZoneInfo("UTC"))

        iso_year, iso_week, _ = final_session_ts.isocalendar()
        period_id = f"W_{iso_year}_W{iso_week:02d}"

        events.append(PeriodEvent(
            period_id=period_id,
            signal_session=final_session_str,
            signal_close_ts=close_utc.isoformat(),
            execution_session=exec_session_str,
            execution_open_ts=open_utc.isoformat(),
            feature_cutoff=close_utc.isoformat(),
            status="COMPLETE",
        ))

        current_monday += pd.Timedelta(days=7)

    return events
