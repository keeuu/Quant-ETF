"""Calendar adapter: past sessions come from the actual bars, future sessions from exchange_calendars where it
has data (XSHG holidays are only recorded through 2026-12-31), otherwise estimated as plain weekdays.

It implements exactly the exchange_calendars methods used by research_v3.cn_transfer (calendar_cn schedule
builders and engine_cn) so the frozen code runs unchanged on it.
"""
from __future__ import annotations

import bisect
import zoneinfo
from datetime import datetime
from typing import Sequence

import pandas as pd

UTC = zoneinfo.ZoneInfo("UTC")


def _ts(x) -> pd.Timestamp:
    t = pd.Timestamp(x)
    if t.tzinfo is not None:
        t = t.tz_localize(None)
    return t.normalize()


class HybridCalendar:
    def __init__(self, base, known_sessions: Sequence[str], tz: zoneinfo.ZoneInfo,
                 open_hm: tuple[int, int], close_hm: tuple[int, int]):
        self.base = base
        self.known = sorted(set(str(s)[:10] for s in known_sessions))
        self._known_set = set(self.known)
        self.tz, self.open_hm, self.close_hm = tz, open_hm, close_hm
        self.base_first = _ts(base.first_session)
        self.base_last = _ts(base.last_session)

    # ---------------------------------------------------------------- coverage
    def covered(self, date) -> bool:
        """True if the exchange calendar has holiday data for `date` (so a future session is not just a guess)."""
        return self.base_first <= _ts(date) <= self.base_last

    # ---------------------------------------------------------------- sessions
    def _unknown(self, a: pd.Timestamp, b: pd.Timestamp) -> list[pd.Timestamp]:
        """Sessions in [a, b] where bars do not decide: exchange calendar if covered, else weekdays."""
        if a > b:
            return []
        out: list[pd.Timestamp] = []
        lo, hi = max(a, self.base_first), min(b, self.base_last)
        if lo <= hi:
            out += [_ts(s) for s in self.base.sessions_in_range(lo, hi)]
        if a < self.base_first:
            out += list(pd.bdate_range(a, min(b, self.base_first - pd.Timedelta(days=1))))
        if b > self.base_last:
            out += list(pd.bdate_range(max(a, self.base_last + pd.Timedelta(days=1)), b))
        return out

    def sessions_in_range(self, start, end) -> pd.DatetimeIndex:
        s, e = _ts(start), _ts(end)
        if s > e:
            return pd.DatetimeIndex([])
        if not self.known:
            return pd.DatetimeIndex(sorted(set(self._unknown(s, e))))
        lo, hi = pd.Timestamp(self.known[0]), pd.Timestamp(self.known[-1])
        parts: list[pd.Timestamp] = []
        if s < lo:
            parts += self._unknown(s, min(e, lo - pd.Timedelta(days=1)))
        parts += [pd.Timestamp(d) for d in self.known if s <= pd.Timestamp(d) <= e]
        if e > hi:
            parts += self._unknown(max(s, hi + pd.Timedelta(days=1)), e)
        return pd.DatetimeIndex(sorted(set(parts)))

    def is_session(self, ts) -> bool:
        t = _ts(ts)
        if self.known and pd.Timestamp(self.known[0]) <= t <= pd.Timestamp(self.known[-1]):
            return t.strftime("%Y-%m-%d") in self._known_set
        return len(self._unknown(t, t)) == 1

    def next_session(self, ts) -> pd.Timestamp:
        t = _ts(ts)
        for span in (15, 60, 400):
            r = self.sessions_in_range(t + pd.Timedelta(days=1), t + pd.Timedelta(days=span))
            if len(r):
                return r[0]
        raise ValueError(f"no session after {ts}")

    def previous_session(self, ts) -> pd.Timestamp:
        t = _ts(ts)
        for span in (15, 60, 400):
            r = self.sessions_in_range(t - pd.Timedelta(days=span), t - pd.Timedelta(days=1))
            if len(r):
                return r[-1]
        raise ValueError(f"no session before {ts}")

    # ---------------------------------------------------------------- open / close
    def _synthetic(self, ts, hm: tuple[int, int]) -> pd.Timestamp:
        t = _ts(ts)
        local = datetime(t.year, t.month, t.day, hm[0], hm[1], tzinfo=self.tz)
        return pd.Timestamp(local).tz_convert("UTC")

    def _from_base(self, ts, kind: str):
        t = _ts(ts)
        if self.covered(t):
            try:
                return pd.Timestamp(getattr(self.base, kind)(t)).tz_convert("UTC")
            except Exception:
                return None
        return None

    def session_open(self, ts) -> pd.Timestamp:
        return self._from_base(ts, "session_open") or self._synthetic(ts, self.open_hm)

    def session_close(self, ts) -> pd.Timestamp:
        return self._from_base(ts, "session_close") or self._synthetic(ts, self.close_hm)
