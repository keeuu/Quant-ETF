"""Calendar-parameterised mirror of research_v2 schedule builders (XSHG for the CN transfer test).

research_v2.calendar.build_complete_weeks / research_v2.schedule.build_weekly_schedule /
build_monthly_matched_schedule call the XNYS singleton internally, so they cannot be pointed at XSHG.
The functions below are line-for-line mirrors with ONE change: the exchange_calendars object is an explicit
argument `cal` instead of get_trading_calendar(). Fed the XNYS calendar and XNYS sessions they reproduce the
v2 output exactly (tests_v3/test_cn_transfer.py). research_v2 is not modified.
"""
from __future__ import annotations

import zoneinfo
from datetime import datetime
from functools import lru_cache
from typing import Mapping, Sequence

import exchange_calendars as ecals
import pandas as pd

from research_v2.calendar import parse_as_of
from research_v2.contracts import PeriodEvent

CN_TZ = zoneinfo.ZoneInfo("Asia/Shanghai")
# exchange_calendars 4.13.2 records XSHG holidays only through 2026.
CN_CALENDAR_START = "2000-01-01"
CN_CALENDAR_END = "2026-12-31"


@lru_cache(maxsize=1)
def get_xshg_calendar(start: str = CN_CALENDAR_START, end: str = CN_CALENDAR_END) -> ecals.ExchangeCalendar:
    return ecals.get_calendar("XSHG", start=start, end=end)


def market_as_of_after_close_cal(cal, session_date, tz, minutes_after_close: int = 1) -> str:
    """Mirror of research_v2.calendar.market_as_of_after_close with explicit calendar and local tz."""
    ts = pd.Timestamp(session_date)
    if cal.is_session(ts):
        close_utc = cal.session_close(ts)
        close_local = close_utc.astimezone(tz)
        return (close_local + pd.Timedelta(minutes=minutes_after_close)).isoformat()
    raise ValueError(f"{session_date} is not a session of the supplied calendar")


def build_complete_weeks_cal(cal, session_dates: Sequence[str], as_of: str | datetime,
                             symbol_dates: Mapping[str, set[str]] | None = None) -> list[PeriodEvent]:
    as_of_utc = parse_as_of(as_of)

    all_sessions_set = set(session_dates)
    if not all_sessions_set:
        return []

    ts_sessions = [pd.Timestamp(s) for s in sorted(all_sessions_set)]
    start_dt = ts_sessions[0]
    end_dt = ts_sessions[-1]

    first_monday = start_dt - pd.Timedelta(days=start_dt.dayofweek)
    last_sunday = end_dt + pd.Timedelta(days=6 - end_dt.dayofweek)

    events: list[PeriodEvent] = []

    current_monday = first_monday
    while current_monday <= last_sunday:
        current_sunday = current_monday + pd.Timedelta(days=6)
        mon_str = current_monday.strftime("%Y-%m-%d")
        sun_str = current_sunday.strftime("%Y-%m-%d")

        expected_in_week = [s.strftime("%Y-%m-%d") for s in cal.sessions_in_range(mon_str, sun_str)]

        if not expected_in_week:
            current_monday += pd.Timedelta(days=7)
            continue

        if any(s not in all_sessions_set for s in expected_in_week):
            current_monday += pd.Timedelta(days=7)
            continue

        final_session_str = expected_in_week[-1]
        final_session_ts = pd.Timestamp(final_session_str)
        close_utc = cal.session_close(final_session_ts).astimezone(zoneinfo.ZoneInfo("UTC"))

        if close_utc > as_of_utc:
            current_monday += pd.Timedelta(days=7)
            continue

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
            current_monday += pd.Timedelta(days=7)
            continue

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


def build_weekly_schedule_cal(cal, session_dates: Sequence[str], as_of: str,
                              symbol_dates: Mapping[str, set[str]] | None = None) -> list[PeriodEvent]:
    events = build_complete_weeks_cal(cal, session_dates, as_of, symbol_dates)
    available_sessions = set(session_dates)

    validated_events: list[PeriodEvent] = []
    for ev in events:
        sig_close = parse_as_of(ev.signal_close_ts)
        exec_open = parse_as_of(ev.execution_open_ts)
        if sig_close >= exec_open:
            raise ValueError(f"Lookahead invariant violated: signal close {sig_close} >= execution open {exec_open}")

        status = "COMPLETE"
        if ev.execution_session not in available_sessions:
            status = "PENDING"

        validated_events.append(PeriodEvent(
            period_id=ev.period_id,
            signal_session=ev.signal_session,
            signal_close_ts=ev.signal_close_ts,
            execution_session=ev.execution_session,
            execution_open_ts=ev.execution_open_ts,
            feature_cutoff=ev.feature_cutoff,
            status=status,
        ))

    return validated_events


def build_monthly_matched_schedule_cal(cal, session_dates: Sequence[str], as_of: str,
                                       weekly_events: Sequence[PeriodEvent]) -> list[PeriodEvent]:
    if not session_dates or not weekly_events:
        return []

    available_sessions = set(session_dates)
    as_of_utc = parse_as_of(as_of)

    ts_sessions = [pd.Timestamp(s) for s in sorted(available_sessions)]
    months_dict: dict[tuple[int, int], list[pd.Timestamp]] = {}
    for ts in ts_sessions:
        key = (ts.year, ts.month)
        months_dict.setdefault(key, []).append(ts)

    monthly_events: list[PeriodEvent] = []

    for (year, month), m_sessions in sorted(months_dict.items()):
        month_end_ts = m_sessions[-1]
        month_end_str = month_end_ts.strftime("%Y-%m-%d")
        month_close_utc = cal.session_close(month_end_ts).astimezone(zoneinfo.ZoneInfo("UTC"))

        if month_close_utc > as_of_utc:
            continue

        eligible_weekly = [w for w in weekly_events if parse_as_of(w.signal_close_ts) <= month_close_utc]

        if not eligible_weekly:
            continue

        latest_weekly = eligible_weekly[-1]

        next_s = cal.next_session(month_end_ts)
        next_s_str = next_s.strftime("%Y-%m-%d")
        next_open_utc = cal.session_open(next_s).astimezone(zoneinfo.ZoneInfo("UTC"))

        status = "COMPLETE" if next_s_str in available_sessions else "PENDING"

        period_id = f"M_MATCH_{year}_{month:02d}"

        monthly_events.append(PeriodEvent(
            period_id=period_id,
            signal_session=month_end_str,
            signal_close_ts=month_close_utc.isoformat(),
            execution_session=next_s_str,
            execution_open_ts=next_open_utc.isoformat(),
            feature_cutoff=latest_weekly.feature_cutoff,
            status=status,
            source_signal_session=latest_weekly.signal_session,
        ))

    return monthly_events
