"""Price panel, point-in-time eligibility, dedup and weekly/monthly target construction (no look-ahead:
every quantity at session i uses sessions <= i only)."""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import guard

MIN_AGE = 250
MIN_TURN60 = 30_000_000.0
TOP_N = 5


class Panel:
    def __init__(self, bars: dict[str, pd.DataFrame], sessions: list[str], index_key: dict[str, str],
                 price_limit: dict[str, float], sector: set[str]):
        guard.assert_dates_allowed(sessions)
        for t, df in bars.items():
            guard.assert_dates_allowed(df["Date"])
        self.sessions = list(sessions)
        self.pos = {d: i for i, d in enumerate(self.sessions)}
        self.tickers = sorted(bars)
        self.col = {t: j for j, t in enumerate(self.tickers)}
        self.index_key, self.price_limit, self.sector = index_key, price_limit, set(sector)
        T, N = len(self.sessions), len(self.tickers)
        idx = pd.Index(self.sessions)
        O = pd.DataFrame(np.nan, index=idx, columns=self.tickers)
        C, TO = O.copy(), O.copy()
        self.off_session_bars = {}
        for t, df in bars.items():
            m = df["Date"].isin(self.pos)
            self.off_session_bars[t] = int((~m).sum())
            d = df[m].set_index("Date")
            O.loc[d.index, t] = d["open"].to_numpy(float)
            C.loc[d.index, t] = d["close"].to_numpy(float)
            TO.loc[d.index, t] = d["turnover"].to_numpy(float)
        self.O, self.C, self.TO = O.to_numpy(), C.to_numpy(), TO.to_numpy()
        CLf = C.ffill()
        self.CLf = CLf.to_numpy()
        self.PC = CLf.shift(1).to_numpy()                       # previous available close (for limit rules)
        has = ~np.isnan(self.C)
        self.has_bar = has
        first = np.array([np.argmax(has[:, j]) if has[:, j].any() else T for j in range(N)])
        self.first_pos = first
        age = np.arange(T)[:, None] - first[None, :] + 1
        self.age = np.where(age > 0, age, 0)
        self.turn60 = TO.fillna(0.0).rolling(60, min_periods=60).mean().to_numpy()
        self.sma = {n: CLf.rolling(n, min_periods=n).mean().to_numpy() for n in (5, 10, 20)}
        lg = np.log(CLf)
        self.sigma20 = lg.diff().rolling(20, min_periods=20).std().to_numpy()
        self.r20 = (CLf / CLf.shift(20) - 1.0).to_numpy()
        self.r60 = (CLf / CLf.shift(60) - 1.0).to_numpy()
        self.score = (self.r20 + self.r60) / 2.0
        self.is_sector = np.array([t in self.sector for t in self.tickers])

    # ---- eligibility -----------------------------------------------------------------------------------------
    def eligible_pre_dedup(self, i: int, min_age: int = MIN_AGE) -> np.ndarray:
        ok = (self.is_sector & self.has_bar[i] & (self.age[i] >= min_age) & (self.turn60[i] >= MIN_TURN60)
              & np.isfinite(self.score[i]) & np.isfinite(self.sma[20][i]) & np.isfinite(self.sigma20[i])
              & (self.sigma20[i] > 1e-9))
        return np.where(ok)[0]

    def eligible_dedup(self, i: int, min_age: int = MIN_AGE) -> list[int]:
        """Eligible columns after keeping, per tracked index, the ETF with the highest 60-session mean turnover
        (ties: lowest ticker)."""
        best: dict[str, int] = {}
        for j in self.eligible_pre_dedup(i, min_age):
            k = self.index_key[self.tickers[j]]
            b = best.get(k)
            if b is None or (self.turn60[i, j], -j) > (self.turn60[i, b], -b):
                best[k] = int(j)
        return sorted(best.values())

    # ---- S1 selection -----------------------------------------------------------------------------------------
    def s1_target(self, i: int, elig: list[int]) -> dict[str, float]:
        cand = [j for j in elig if self.CLf[i, j] > self.sma[20][i, j]]
        cand.sort(key=lambda j: (-self.score[i, j], j))
        top = cand[:TOP_N]
        if not top:
            return {}
        inv = np.array([1.0 / self.sigma20[i, j] for j in top])
        w = (len(top) / TOP_N) * inv / inv.sum()
        return {self.tickers[j]: float(x) for j, x in zip(top, w)}

    # ---- amendment 1: academic momentum (skip the most recent 21 sessions) ----------------------------------
    def skip_mom(self, i: int, lookback: int) -> np.ndarray:
        """close[i-21] / close[i-lookback] - 1 on the forward-filled series (uses sessions <= i-21 only)."""
        if i - lookback < 0:
            return np.full(len(self.tickers), np.nan)
        return self.CLf[i - 21] / self.CLf[i - lookback] - 1.0

    def amend_target(self, i: int, elig: list[int], lookback: int, curve=None, dual: bool = False) -> dict[str, float]:
        sc = self.skip_mom(i, lookback)
        cand = [j for j in elig if np.isfinite(sc[j])]
        if dual:
            cash = 1.0
            for k in range(i - lookback + 1, i - 21 + 1):
                cash *= curve.accrual_factor(self.sessions[k - 1], self.sessions[k])
            cash -= 1.0
            cand = [j for j in cand if sc[j] > cash]
        cand_all = sorted([j for j in elig if np.isfinite(sc[j])], key=lambda j: (-sc[j], j))[:TOP_N]
        if dual:
            cand_all = [j for j in cand_all if j in set(cand)]
        return {self.tickers[j]: 1.0 / TOP_N for j in cand_all}
