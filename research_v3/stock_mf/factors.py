"""Factors, monthly universe, signal calendar, composite score and portfolio selection for stock_mf.

Every function uses only panel rows <= the signal row (verified by the look-ahead tests).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .panel import Panel

REV_WIN, VOL_WIN, TURN_WIN = 20, 60, 20
UNI_SIZE, MIN_BARS, AMT_WIN = 1000, 250, 60
TOP_N, KEEP_RANK = 10, 20
WIN_LO, WIN_HI = 1.0, 99.0
SMALL_LOT_BUDGET = 2500.0


# ------------------------------------------------------------------------------------------------ factors
def factors_at(p: Panel, s: int) -> dict:
    """Raw factor vectors (length N) as of the close of session row s. NaN when not computable.
    REV     = -(20-session total return of the carried-forward adjusted close)
    LOWVOL  = -std (ddof=1) of the last 60 daily returns of the carried-forward adjusted close
    LOWTURN = -mean over the last 20 sessions of volume/float_shares (0 volume on sessions without a bar)
    SIZE    = -log(close * float_shares) at the signal close"""
    N = p.close.shape[1]
    nan = np.full(N, np.nan)
    if s < VOL_WIN:
        return {"REV": nan, "LOWVOL": nan, "LOWTURN": nan, "SIZE": nan}
    A = p.adj_close_ff[s - VOL_WIN:s + 1]
    with np.errstate(invalid="ignore", divide="ignore"):
        rev = -(A[-1] / A[-1 - REV_WIN] - 1.0)
        rets = A[1:] / A[:-1] - 1.0
        vol = -np.std(rets, axis=0, ddof=1)               # NaN propagates: all 60 returns required
        fs = p.float_sh[s]
        v = np.nan_to_num(p.volume[s - TURN_WIN + 1:s + 1], nan=0.0)
        turn = v / p.float_sh[s - TURN_WIN + 1:s + 1]
        lowturn = -np.mean(turn, axis=0)
        size = -np.log(p.close[s] * fs)
    for a in (rev, vol, lowturn, size):
        a[~np.isfinite(a)] = np.nan
    return {"REV": rev, "LOWVOL": vol, "LOWTURN": lowturn, "SIZE": size}


def winsorize_z(x: np.ndarray, lo: float = WIN_LO, hi: float = WIN_HI) -> np.ndarray:
    """Winsorise at the cross-sectional lo/hi percentiles, then z-score (population std)."""
    a, b = np.percentile(x, [lo, hi])
    w = np.clip(x, a, b)
    sd = w.std()
    return (w - w.mean()) / sd if sd > 0 else np.zeros_like(w)


FACTOR_NAMES = ["REV", "LOWVOL", "LOWTURN", "SIZE"]


def composite(fac: dict, members: np.ndarray, names=None) -> np.ndarray:
    """Equal-weight mean of the four winsorised z-scores over `members` (bool N) with all four factors finite.
    Returns length-N vector, NaN for non-members / missing factors."""
    names = list(names or FACTOR_NAMES)
    ok = members.copy()
    for k in names:
        ok &= np.isfinite(fac[k])
    out = np.full(len(members), np.nan)
    if ok.sum() < 3:
        return out
    z = np.vstack([winsorize_z(fac[k][ok]) for k in names])
    out[ok] = z.mean(axis=0)
    return out


# ------------------------------------------------------------------------------------------------ universe / calendar
def month_end_indices(sessions: list) -> list:
    d = pd.to_datetime(pd.Series(sessions))
    key = d.dt.year * 100 + d.dt.month
    last = np.where(key.to_numpy()[1:] != key.to_numpy()[:-1])[0]
    return [int(i) for i in last] + [len(sessions) - 1]


def monthly_signals(sessions: list, universes: dict, start_date: str) -> list:
    """Amendment 1: last session of each month (with a universe from the previous month-end available)."""
    return [i for i in month_end_indices(sessions) if sessions[i] >= start_date and universe_for_signal(universes, i) is not None]


def weekly_signal_indices(sessions: list) -> list:
    """Last session of each ISO week."""
    iso = pd.to_datetime(pd.Series(sessions)).dt.isocalendar()
    key = (iso["year"] * 100 + iso["week"]).to_numpy()
    last = list(np.where(key[1:] != key[:-1])[0]) + [len(sessions) - 1]
    return [int(i) for i in last]


def universe_at(p: Panel, m: int, size: int = UNI_SIZE) -> np.ndarray:
    """Boolean membership (N) of the universe rebuilt at the close of row m.
    Eligible: >= 250 bars strictly before m, a bar with volume > 0 at m, not inferred-ST, float cap known.
    Composite rank = mean(rank by float cap desc, rank by 60-session mean turnover amount desc); keep top `size`."""
    N = p.close.shape[1]
    cap = p.close[m] * p.float_sh[m]
    lo = max(0, m - AMT_WIN + 1)
    amt = np.nan_to_num(p.amount[lo:m + 1], nan=0.0).sum(axis=0) / AMT_WIN
    elig = (p.bars_before[m] >= MIN_BARS) & p.has_bar[m] & ~p.st[m] & np.isfinite(cap) & (cap > 0)
    out = np.zeros(N, bool)
    idx = np.where(elig)[0]
    if len(idx) == 0:
        return out
    r_cap = pd.Series(cap[idx]).rank(ascending=False, method="average").to_numpy()
    r_amt = pd.Series(amt[idx]).rank(ascending=False, method="average").to_numpy()
    comp = (r_cap + r_amt) / 2.0
    order = np.lexsort((idx, -cap[idx], comp))       # comp asc, then larger cap, then code index
    out[idx[order[:size]]] = True
    return out


def build_universes(p: Panel, size: int = UNI_SIZE, first_row: int = 0) -> dict:
    """{month_end_row: bool membership}"""
    return {m: universe_at(p, m, size) for m in month_end_indices(p.sessions) if m >= first_row}


def universe_for_signal(universes: dict, s: int):
    """Universe rebuilt at the latest month-end strictly before row s ("used for the following month")."""
    ms = [m for m in universes if m < s]
    return universes[max(ms)] if ms else None


def biweekly_signals(sessions: list, universes: dict, start_date: str) -> list:
    """Every second weekly signal, starting from the first week (last session >= start_date) that has a universe."""
    wk = [i for i in weekly_signal_indices(sessions) if sessions[i] >= start_date and universe_for_signal(universes, i) is not None]
    return wk[::2]


# ------------------------------------------------------------------------------------------------ selection
def rank_table(comp: np.ndarray, codes: list) -> pd.DataFrame:
    """Rows sorted best-first (composite desc, ties by code asc). Columns: j (panel column), score, rank (1 = best)."""
    ok = np.where(np.isfinite(comp))[0]
    df = pd.DataFrame({"j": ok, "score": comp[ok], "code": [codes[j] for j in ok]})
    df = df.sort_values(["score", "code"], ascending=[False, True], kind="mergesort").reset_index(drop=True)
    df["rank"] = np.arange(1, len(df) + 1)
    return df[["j", "code", "score", "rank"]]


def select_targets(table: pd.DataFrame, held, n: int = TOP_N, keep_rank: int = KEEP_RANK, affordable=None) -> list:
    """Buffer rule. `table` from rank_table; `held` iterable of panel columns; `affordable` optional bool array over
    panel columns (stocks failing it are skipped BEFORE ranking, so ranks are among affordable stocks).
    Target = held stocks whose rank <= keep_rank (kept) + best-ranked non-held stocks until n names. Returns list of j."""
    t = table
    if affordable is not None:
        t = t[affordable[t["j"].to_numpy()]].copy()
        t["rank"] = np.arange(1, len(t) + 1)
    held = set(int(h) for h in held)
    kept = [int(j) for j, r in zip(t["j"], t["rank"]) if int(j) in held and r <= keep_rank]
    kept = kept[:n]
    out = list(kept)
    for j in t["j"]:
        if len(out) >= n:
            break
        if int(j) not in held:
            out.append(int(j))
    return out


def affordable_mask(p: Panel, s: int, budget: float = SMALL_LOT_BUDGET) -> np.ndarray:
    """One board lot at the signal close costs at most `budget` CNY."""
    with np.errstate(invalid="ignore"):
        return (p.close[s] * p.lot) <= budget
