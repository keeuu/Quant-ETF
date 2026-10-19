"""Duck-typed session calendar built from an explicit list of session dates, for the *_cal schedule builders
and the engine. Open/close timestamps are nominal (open 14:30 UTC, close 21:00 UTC); only their ordering and
the elapsed-years arithmetic matter."""
from __future__ import annotations

import bisect
from typing import Sequence

import pandas as pd


class ProxyCalendar:
    def __init__(self, sessions: Sequence[str]):
        self.sessions = sorted(sessions)
        self._ts = [pd.Timestamp(s) for s in self.sessions]

    def sessions_in_range(self, a, b):
        a, b = pd.Timestamp(a).strftime("%Y-%m-%d"), pd.Timestamp(b).strftime("%Y-%m-%d")
        i, j = bisect.bisect_left(self.sessions, a), bisect.bisect_right(self.sessions, b)
        return self._ts[i:j]

    def is_session(self, ts) -> bool:
        s = pd.Timestamp(ts).strftime("%Y-%m-%d")
        i = bisect.bisect_left(self.sessions, s)
        return i < len(self.sessions) and self.sessions[i] == s

    def session_close(self, ts):
        return pd.Timestamp(pd.Timestamp(ts).strftime("%Y-%m-%d") + " 21:00", tz="UTC")

    def session_open(self, ts):
        return pd.Timestamp(pd.Timestamp(ts).strftime("%Y-%m-%d") + " 14:30", tz="UTC")

    def next_session(self, ts):
        s = pd.Timestamp(ts).strftime("%Y-%m-%d")
        i = bisect.bisect_right(self.sessions, s)
        if i < len(self.sessions):
            return self._ts[i]
        nxt = pd.Timestamp(s) + pd.Timedelta(days=1)   # beyond the data: next weekday (event will be PENDING)
        while nxt.dayofweek >= 5:
            nxt += pd.Timedelta(days=1)
        return nxt


def as_of_after_close(cal: ProxyCalendar, date: str) -> str:
    return (cal.session_close(pd.Timestamp(date)) + pd.Timedelta(minutes=1)).isoformat()
