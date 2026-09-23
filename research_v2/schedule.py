"""Weekly and monthly matched execution schedule builders for Research V2.

Guarantees signal_close_ts < execution_open_ts, prevents same-day close execution,
and matches monthly cadence to the latest completed weekly signal.
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence
import pandas as pd
import zoneinfo

from .calendar import build_complete_weeks, get_trading_calendar, parse_as_of
from .contracts import PeriodEvent

TIMEZONE_STR = "America/New_York"
NY_TZ = zoneinfo.ZoneInfo(TIMEZONE_STR)


def build_weekly_schedule(
    session_dates: Sequence[str],
    as_of: str,
    symbol_dates: Mapping[str, set[str]] | None = None,
) -> list[PeriodEvent]:
    """Construct the weekly decision and execution schedule.

    If the execution session for the terminal week is outside the dataset snapshot,
    it is marked PENDING. All historical events must have next_session strictly after signal_close.
    """
    cal = get_trading_calendar()
    events = build_complete_weeks(session_dates, as_of, symbol_dates)
    available_sessions = set(session_dates)

    validated_events: list[PeriodEvent] = []
    for ev in events:
        # Check signal_close_ts < execution_open_ts
        sig_close = parse_as_of(ev.signal_close_ts)
        exec_open = parse_as_of(ev.execution_open_ts)
        if sig_close >= exec_open:
            raise ValueError(f"Lookahead invariant violated: signal close {sig_close} >= execution open {exec_open}")

        # Check if execution session is available in snapshot
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


def build_monthly_matched_schedule(
    session_dates: Sequence[str],
    as_of: str,
    weekly_events: Sequence[PeriodEvent],
) -> list[PeriodEvent]:
    """Construct monthly matched cadence schedule.

    Each month-end uses the latest completed weekly signal available as of month-end close.
    Does NOT calculate new signals from partial mid-week data at month end.
    Execution takes place on next-session open in the following month.
    """
    if not session_dates or not weekly_events:
        return []

    cal = get_trading_calendar()
    available_sessions = set(session_dates)
    as_of_utc = parse_as_of(as_of)

    # Group all available sessions by (Year, Month)
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
            # Month is incomplete as of cutoff
            continue

        # Find latest completed weekly event whose signal_close_ts <= month_close_utc
        eligible_weekly = [
            w for w in weekly_events
            if parse_as_of(w.signal_close_ts) <= month_close_utc
        ]

        if not eligible_weekly:
            # No completed weekly signal prior to this month-end
            continue

        latest_weekly = eligible_weekly[-1]

        # Execution is on the next trading session after month end
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
