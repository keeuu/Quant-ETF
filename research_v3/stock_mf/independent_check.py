"""Independent pandas recompute of the SMALL-account top-10 selection (fresh book, no holdings) on a signal date.

Deliberately avoids the dense-array code path of panel/factors: every stock is handled as its own pandas Series, the
universe, inferred-ST flag, four factors, winsorise / z-score, composite and affordability filter are recomputed with
plain pandas / numpy per-stock operations. Input: the StockBars list from panel.load_dev_stocks (same adjusted prices)."""
from __future__ import annotations

import numpy as np
import pandas as pd


def _asof(rec: pd.Series, date: str):
    if rec is None or len(rec) == 0:
        return np.nan
    r = rec[rec.index <= date]
    return float(r.iloc[-1]) if len(r) else np.nan


def independent_top(stocks, sessions, s_date: str, month_end_date: str, n: int = 10, size: int = 1000, budget: float = 2500.0):
    sess = pd.Index(sessions)
    pos_m = sess.get_loc(month_end_date)
    pos_s = sess.get_loc(s_date)
    # ---------------- universe at month_end_date
    rows = []
    for sb in stocks:
        d = sb.df
        if month_end_date not in d.index:
            continue
        if (d.index < month_end_date).sum() < 250 or d.loc[month_end_date, "volume"] <= 0:
            continue
        win = sess[max(0, pos_m - 59):pos_m + 1]
        adj_prev = d["adj_close"].shift(1)
        r = (d["adj_close"] / adj_prev - 1.0).reindex(win)
        up = (r > 0) & ((d["close"] - d["high"]).abs().reindex(win) < 1e-6)
        dn = (r < 0) & ((d["close"] - d["low"]).abs().reindex(win) < 1e-6)
        lim = ((r.abs() >= 0.0485) & (r.abs() <= 0.0515) & (up | dn)).sum()
        big = (r.abs() > 0.055).sum()
        if lim >= 1 and big == 0:
            continue                                                  # inferred ST
        fl = _asof(sb.float_records.dropna(), month_end_date)
        cap = d.loc[month_end_date, "close"] * fl
        if not (np.isfinite(cap) and cap > 0):
            continue
        amt = d["amount"].reindex(win).fillna(0.0).sum() / 60.0
        rows.append((sb.code, cap, amt))
    u = pd.DataFrame(rows, columns=["code", "cap", "amt"])
    u["rc"] = u["cap"].rank(ascending=False)
    u["ra"] = u["amt"].rank(ascending=False)
    u["c"] = (u["rc"] + u["ra"]) / 2
    u = u.sort_values(["c", "cap", "code"], ascending=[True, False, True]).head(size)
    members = set(u["code"])
    # ---------------- factors at s_date
    by = {sb.code: sb for sb in stocks}
    fac = []
    for code in members:
        sb = by[code]
        d = sb.df
        if s_date not in d.index or d.loc[s_date, "volume"] <= 0:
            continue
        w = sess[pos_s - 60:pos_s + 1]
        a = d["adj_close"].reindex(sess[:pos_s + 1]).ffill().reindex(w)
        if a.isna().any():
            continue
        rev = -(a.iloc[-1] / a.iloc[-21] - 1.0)
        vol = -a.pct_change().dropna().std(ddof=1)
        w20 = sess[pos_s - 19:pos_s + 1]
        fl = pd.Series([_asof(sb.float_records.dropna(), x) for x in w20], index=w20)
        if fl.isna().any():
            continue
        turn = -(d["volume"].reindex(w20).fillna(0.0) / fl).mean()
        size_f = -np.log(d.loc[s_date, "close"] * fl.iloc[-1])
        fac.append((code, rev, vol, turn, size_f))
    f = pd.DataFrame(fac, columns=["code", "REV", "LOWVOL", "LOWTURN", "SIZE"]).dropna()
    for c in ["REV", "LOWVOL", "LOWTURN", "SIZE"]:
        lo, hi = np.percentile(f[c], [1, 99])
        w = f[c].clip(lo, hi)
        f[c + "_z"] = (w - w.mean()) / w.std(ddof=0)
    f["comp"] = f[[c + "_z" for c in ["REV", "LOWVOL", "LOWTURN", "SIZE"]]].mean(axis=1)
    f["lot_cost"] = [by[c].df.loc[s_date, "close"] * (200 if (by[c].ex == "SH" and c.startswith("68")) else 100) for c in f["code"]]
    f = f[f["lot_cost"] <= budget].sort_values(["comp", "code"], ascending=[False, True])
    return f["code"].head(n).tolist(), f
