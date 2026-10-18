"""Run independent sub-portfolios (tranches) and sum NAVs. Costs per sub-portfolio, no netting."""
from __future__ import annotations

import dataclasses
from typing import Any, Sequence

import pandas as pd

from research_v2.calendar import get_trading_calendar
from research_v2.contracts import DatasetView, SignalFrame
from research_v2.metrics import compute_backtest_metrics

from .cash_yield import CashYieldCurve
from .config import V3Config
from .engine import run_backtest_v3


def run_tranches(view: DatasetView, frames: Sequence[SignalFrame], cfg: V3Config,
                 curve: CashYieldCurve | None = None) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    n = len(frames)
    sub_cfg = dataclasses.replace(cfg, initial_nav=cfg.initial_nav / n)
    navs, rebs = [], []
    for f in frames:
        nav, reb, _ = run_backtest_v3(view, f, sub_cfg, curve)
        navs.append(nav)
        rebs.append(reb)
    dates = navs[0]["date"]
    tot = navs[0][["date"]].copy()
    tot["nav"] = sum(x["nav"].to_numpy() for x in navs)
    tot["cash"] = sum(x["cash"].to_numpy() for x in navs)
    tot["fee"] = sum(x["fee"].to_numpy() for x in navs)
    tot["cash_interest"] = sum(x["cash_interest"].to_numpy() for x in navs)
    tot["gross_exposure"] = 1.0 - tot["cash"] / tot["nav"]
    tot["rebalance_flag"] = [any(x["rebalance_flag"].iloc[i] for x in navs) for i in range(len(tot))]
    rets = tot["nav"].pct_change()
    rets.iloc[0] = tot["nav"].iloc[0] / cfg.initial_nav - 1.0
    tot["daily_return"] = rets
    parts = []
    for r in rebs:
        if len(r):
            r = r.copy()
            for c in ("one_way_turnover", "gross_traded_ratio"):  # express relative to total capital
                r[c] = r[c] / n
            parts.append(r)
    reb = pd.concat(parts, ignore_index=True) if parts else rebs[0]
    cal = get_trading_calendar()
    metrics = compute_backtest_metrics(
        daily_nav=tot, rebalances=reb, initial_nav=cfg.initial_nav,
        first_session_open_utc=cal.session_open(pd.Timestamp(dates.iloc[0])),
        last_session_close_utc=cal.session_close(pd.Timestamp(dates.iloc[-1])))
    metrics["total_cash_interest"] = float(tot["cash_interest"].sum())
    return tot, reb, metrics
