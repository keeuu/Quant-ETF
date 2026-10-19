"""Performance metrics for one simulation result and one period slice."""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

MIN_YEAR_SESSIONS = 200
ANN = 252.0


def daily_rf(dates: list[str], curve) -> np.ndarray:
    return np.array([0.0] + [curve.accrual_factor(dates[i - 1], dates[i]) - 1.0 for i in range(1, len(dates))])


def calendar_year_table(nav: pd.DataFrame) -> pd.DataFrame:
    yr = nav["date"].str[:4].astype(int)
    rows = []
    for y, g in nav.groupby(yr):
        rows.append({"year": int(y), "ret": float(np.prod(1.0 + g["ret"].to_numpy()) - 1.0), "sessions": int(len(g)),
                     "full_year": bool(len(g) >= MIN_YEAR_SESSIONS)})
    return pd.DataFrame(rows).set_index("year")


def max_dd(navs: np.ndarray, dates: list[str]):
    peak = np.maximum.accumulate(navs)
    dd = 1.0 - navs / peak
    k = int(dd.argmax())
    p = int(np.argmax(navs[:k + 1])) if k > 0 else 0
    return float(dd[k]), dates[p], dates[k]


def xsharpe(ret: np.ndarray, rf: np.ndarray) -> float:
    ex = ret - rf
    sd = ex.std(ddof=1)
    return float(ex.mean() / sd * math.sqrt(ANN)) if sd > 1e-12 else float("nan")


def slice_metrics(res, rf: np.ndarray, start: str, end: str, capital: float, include_counters: bool = False) -> tuple[dict, np.ndarray]:
    nav = res.nav
    m = (nav["date"] >= start) & (nav["date"] <= end)
    idx = np.where(m)[0]
    sl = nav.iloc[idx]
    base = capital if idx[0] == 0 else float(nav["nav"].iloc[idx[0] - 1])
    ret = sl["ret"].to_numpy(float)
    if idx[0] > 0:  # first return of slice is relative to the previous (in-sample) nav: already so by construction
        pass
    dates = sl["date"].tolist()
    days = (pd.Timestamp(dates[-1]) - pd.Timestamp(dates[0])).days + 1
    years = days / 365.25
    end_nav = float(sl["nav"].iloc[-1])
    cagr = (end_nav / base) ** (1.0 / years) - 1.0
    navs = np.concatenate([[base], sl["nav"].to_numpy(float)])
    mdd, pk, tr = max_dd(navs, ["<start>"] + dates)
    ex = ret - rf[idx]
    yt = calendar_year_table(sl)
    full = yt[yt["full_year"]]
    trades = [t for t in res.trades if start <= t["date"] <= end]
    notional = sum(t["notional"] for t in trades)
    fees = sum(t["fee"] for t in trades)
    trips = [t for t in res.trips if t["exit"] is not None and start <= t["exit"] <= end]
    pnl = [t["proceeds"] - t["spent"] for t in trips]
    hold = [(pd.Timestamp(t["exit"]) - pd.Timestamp(t["entry"])).days for t in trips]
    out = {
        "start": dates[0], "end": dates[-1], "n_sessions": len(dates), "years": years,
        "cagr": cagr, "ann_vol": float(ret.std(ddof=1) * math.sqrt(ANN)), "xsharpe": xsharpe(ret, rf[idx]),
        "max_drawdown": mdd, "mdd_peak_date": pk, "mdd_trough_date": tr,
        "worst_year": float(full["ret"].min()) if len(full) else float("nan"),
        "worst_year_label": int(full["ret"].idxmin()) if len(full) else None,
        "ann_one_way_turnover": float(notional / 2.0 / sl["nav"].mean() / years),
        "total_fees": fees, "n_fills": len(trades), "n_round_trips": len(trips),
        "win_rate": float(np.mean([p > 0 for p in pnl])) if pnl else float("nan"),
        "avg_holding_days": float(np.mean(hold)) if hold else float("nan"),
        "mean_cash_weight": float(np.mean(sl["cash"].to_numpy() / sl["nav"].to_numpy())),
        "final_nav": end_nav,
    }
    if include_counters:
        out.update({k: v for k, v in res.counters.items()})
        out["open_positions_at_end"] = len(res.final_shares)
    return out, ex
