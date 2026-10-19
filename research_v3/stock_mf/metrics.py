"""Performance metrics for stock_mf simulation results."""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

ANN = 252.0


def max_dd(navs: np.ndarray, dates: list):
    peak = np.maximum.accumulate(navs)
    dd = 1.0 - navs / peak
    k = int(dd.argmax())
    pk = int(np.argmax(navs[:k + 1])) if k > 0 else 0
    return float(dd[k]), dates[pk], dates[k]


def xsharpe(ret: np.ndarray, rf: np.ndarray) -> float:
    ex = np.asarray(ret) - np.asarray(rf)
    sd = ex.std(ddof=1)
    return float(ex.mean() / sd * math.sqrt(ANN)) if sd > 1e-12 else float("nan")


def calendar_year_returns(dates: list, ret: np.ndarray) -> pd.Series:
    s = pd.Series(np.asarray(ret) + 1.0, index=pd.to_datetime(dates))
    return s.groupby(s.index.year).prod() - 1.0


def period_metrics(nav: pd.DataFrame, rf: np.ndarray, start: str, end: str, base_nav: float | None = None) -> dict:
    """nav: columns date, nav, cash, n_hold, ret (+ optional trade counters). rf aligned to nav rows."""
    m = ((nav["date"] >= start) & (nav["date"] <= end)).to_numpy()
    idx = np.where(m)[0]
    sl = nav.iloc[idx]
    if base_nav is None:
        base_nav = float(nav["nav"].iloc[idx[0] - 1]) if idx[0] > 0 else float(nav["nav"].iloc[0] / (1 + nav["ret"].iloc[0]))
    ret = sl["nav"].to_numpy(float)
    ret = np.concatenate([[ret[0] / base_nav - 1.0], ret[1:] / ret[:-1] - 1.0])
    dates = sl["date"].tolist()
    years = ((pd.Timestamp(dates[-1]) - pd.Timestamp(dates[0])).days + 1) / 365.25
    end_nav = float(sl["nav"].iloc[-1])
    navs = np.concatenate([[base_nav], sl["nav"].to_numpy(float)])
    mdd, pk, tr = max_dd(navs, ["<start>"] + dates)
    cy = calendar_year_returns(dates, ret)
    full = [y for y in cy.index if (np.array([d[:4] for d in dates]) == str(y)).sum() >= 200]
    worst = float(cy.loc[full].min()) if full else float("nan")
    mean_nav = float(sl["nav"].mean())
    out = {
        "start": dates[0], "end": dates[-1], "sessions": len(dates), "years": round(years, 3),
        "cagr": (end_nav / base_nav) ** (1 / years) - 1.0,
        "ann_vol": float(ret.std(ddof=1) * math.sqrt(ANN)),
        "xsharpe": xsharpe(ret, rf[idx]),
        "max_drawdown": mdd, "mdd_peak": pk, "mdd_trough": tr,
        "worst_full_year": worst, "worst_year_label": int(cy.loc[full].idxmin()) if full else None,
        "end_nav": end_nav,
    }
    if "buy_notional" in sl.columns:
        out["ann_one_way_turnover"] = float((sl["buy_notional"].sum() + sl["sell_notional"].sum()) / 2.0 / mean_nav / years)
        out["cost_drag_ann"] = float((sl["fees"].sum() + sl["slippage"].sum()) / mean_nav / years)
        out["fees_only_drag_ann"] = float(sl["fees"].sum() / mean_nav / years)
        out["n_trades"] = int(sl["fills"].sum())
        out["blocked_orders"] = int(sl["blocked"].sum())
        out["mean_holdings"] = float(sl["n_hold"].mean())
        out["mean_cash_weight"] = float((sl["cash"] / sl["nav"]).mean())
    return out, ret, cy


def index_nav_frame(idx_close: pd.Series, sessions: list) -> pd.DataFrame:
    """Buy-and-hold price-index nav aligned to sessions (rows before the index exists are dropped)."""
    s = idx_close.reindex(sessions).ffill()
    s = s.dropna()
    nav = pd.DataFrame({"date": s.index.tolist(), "nav": s.to_numpy(float)})
    nav["ret"] = nav["nav"].pct_change()
    return nav
