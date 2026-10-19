"""Proxy construction for the us_long study: total-return indices, synthetic par-bond returns, session calendar."""
from __future__ import annotations

import bisect
import math
from dataclasses import dataclass, field
from typing import Mapping, Sequence

import numpy as np
import pandas as pd

UNIVERSE = ("SPY", "QQQ", "IWM", "EFA", "EEM", "GLD", "TLT", "IEF")
GROUPS = {"US_EQ": ["SPY", "QQQ", "IWM"], "INTL_EQ": ["EFA", "EEM"], "GOLD": ["GLD"], "BONDS": ["TLT", "IEF"]}
EQUITY_GROUPS = ("US_EQ", "INTL_EQ")
TRADING_DAYS = 252.0


# ---------------------------------------------------------------- synthetic bond
def par_bond_price(coupon: float, y: float, maturity_years: float) -> float:
    """Price per 1 of par of a bond paying `coupon` (annual rate, semi-annual payments) maturing in
    `maturity_years`, discounted at annual yield `y` (semi-annual compounding). Rates are decimals."""
    n = int(round(2 * maturity_years))
    i = y / 2.0
    if abs(i) < 1e-12:
        return 1.0 + coupon / 2.0 * n
    v = (1.0 + i) ** (-n)
    return (coupon / 2.0) * (1.0 - v) / i + v


def synthetic_bond_returns(yield_pct: pd.Series, maturity_years: float, max_gap_days: int = 10) -> pd.Series:
    """r_t = y_{t-1}/252 + [P(coupon=y_{t-1}, yield=y_t, M) - 1] on consecutive observed yield dates
    (yield_pct in percent, index = 'YYYY-MM-DD'). No roll-down. The series is cut at the first
    gap > max_gap_days calendar days (observations after a break are discarded)."""
    s = yield_pct.dropna().sort_index()
    d = pd.to_datetime(s.index)
    gaps = np.diff(d.values).astype("timedelta64[D]").astype(int)
    brk = np.where(gaps > max_gap_days)[0]
    if len(brk):
        s = s.iloc[: brk[0] + 1]
    y = s.values / 100.0
    r = np.full(len(y), np.nan)
    for t in range(1, len(y)):
        r[t] = y[t - 1] / TRADING_DAYS + par_bond_price(y[t - 1], y[t], maturity_years) - 1.0
    return pd.Series(r, index=s.index)


def returns_to_index(r: pd.Series, base: float = 100.0) -> pd.Series:
    """Total-return index: base at the first observation, then cumulative product of (1+r)."""
    r = r.copy()
    r.iloc[0] = 0.0
    return base * (1.0 + r).cumprod()


# ---------------------------------------------------------------- proxy set
@dataclass
class ProxySet:
    sessions: list
    signal: dict            # sym -> Series(index=sessions) level, NaN where no observation (and before first date)
    valuation: dict         # sym -> Series(index=sessions) level forward-filled from first observation
    first_date: dict        # sym -> first observation date or None (unavailable)
    fills: dict             # sym -> number of sessions (>= first date) valued by forward fill
    notes: dict = field(default_factory=dict)


def build_proxies(*, ff3: pd.DataFrame, size_lo30: pd.Series, devxus: pd.DataFrame, ixic: pd.Series, gold: pd.Series,
                  dgs10: pd.Series, dgs30: pd.Series, data_start: str, score_end: str,
                  gold_first: str | None = None) -> ProxySet:
    """Build the 8 proxies on the sessions where the SPY proxy has a return (data_start..score_end)."""
    spy_r = ((ff3["Mkt-RF"] + ff3["RF"]) / 100.0)
    ds = max(data_start, gold_first or data_start)
    spy_r = spy_r[(spy_r.index >= ds) & (spy_r.index <= score_end)]
    sessions = list(spy_r.index)
    idx: dict[str, pd.Series] = {}
    idx["SPY"] = returns_to_index(spy_r)

    def clean_ret(s: pd.Series) -> pd.Series:   # French missing = -99.99 / -999
        s = s[(s > -99.0)]
        return s / 100.0

    iw = clean_ret(size_lo30)
    iw = iw[(iw.index >= ds) & (iw.index <= score_end)]
    idx["IWM"] = returns_to_index(iw)
    ixic = ixic[(ixic.index >= ds) & (ixic.index <= score_end)]
    idx["QQQ"] = ixic
    d = devxus.copy()
    de = ((d["Mkt-RF"] + d["RF"]))
    de = de[de > -99.0] / 100.0
    de = de[(de.index >= ds) & (de.index <= score_end)]
    idx["EFA"] = returns_to_index(de)
    g = gold[(gold.index >= ds) & (gold.index <= score_end)]
    idx["GLD"] = g
    b10 = synthetic_bond_returns(dgs10[dgs10.index <= score_end], 10)
    b10 = b10[b10.index >= ds]
    idx["IEF"] = returns_to_index(b10.iloc[0:]) if len(b10) else b10
    b30 = synthetic_bond_returns(dgs30[dgs30.index <= score_end], 30)
    b30 = b30[b30.index >= ds]
    idx["TLT"] = returns_to_index(b30)
    sig, val, first, fills = {}, {}, {}, {}
    for sym in UNIVERSE:
        s = idx.get(sym)
        if s is None or len(s) == 0:
            sig[sym] = pd.Series(np.nan, index=sessions)
            val[sym] = sig[sym].copy()
            first[sym], fills[sym] = None, 0
            continue
        s = s[~s.index.duplicated(keep="last")]
        aligned = s.reindex(sessions)
        f0 = s.index[0]
        # first date on the session grid (first session >= first observation)
        first_session = next((x for x in sessions if x >= f0), None)
        # observations on non-session dates are not used (no new observation on a non-session day)
        sig[sym] = aligned
        v = aligned.ffill()
        val[sym] = v
        first[sym] = first_session
        valid_from = aligned.first_valid_index()
        first[sym] = valid_from
        fills[sym] = int(aligned.loc[valid_from:].isna().sum()) if valid_from else 0
    return ProxySet(sessions=sessions, signal=sig, valuation=val, first_date=first, fills=fills)


def signal_bars(ps: ProxySet) -> dict:
    return {s: pd.DataFrame({"Date": ps.sessions, "adj_close": ps.signal[s].values, "adj_open": ps.signal[s].values})
            for s in UNIVERSE}


def price_dicts(ps: ProxySet, execution: str = "close") -> tuple[dict, dict]:
    """(exec_price, close_price) dicts sym -> {date: level}. Valuation uses forward-filled levels.
    execution='close': execute at the close of the execution session (open == close)."""
    close = {s: {d: float(v) for d, v in zip(ps.sessions, ps.valuation[s].values) if v == v} for s in UNIVERSE}
    return (close if execution == "close" else None), close
