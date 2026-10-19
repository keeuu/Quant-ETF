"""Writes artifacts_dev/summary.md (Chinese) from the artifacts produced by runner.main()."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

OUT = Path(__file__).resolve().parent / "artifacts_dev"
RUN_ORDER = ["STRAT_SMALL_NOMIN_10", "STRAT_SMALL_NOMIN_20", "STRAT_SMALL_MIN5_10", "STRAT_SMALL_MIN5_20", "STRAT_LARGE_10",
             "STRAT_LARGE_20", "EW_UNIVERSE_10", "EW_UNIVERSE_20", "CSI1000", "CSI500"]
LABEL = {"STRAT_SMALL_NOMIN_10": "策略 SMALL/NO_MIN/10bp(决策版)", "STRAT_SMALL_NOMIN_20": "策略 SMALL/NO_MIN/20bp",
         "STRAT_SMALL_MIN5_10": "策略 SMALL/MIN5/10bp", "STRAT_SMALL_MIN5_20": "策略 SMALL/MIN5/20bp",
         "STRAT_LARGE_10": "策略 LARGE/10bp", "STRAT_LARGE_20": "策略 LARGE/20bp",
         "EW_UNIVERSE_10": "EW_UNIVERSE 10bp", "EW_UNIVERSE_20": "EW_UNIVERSE 20bp", "CSI1000": "中证1000(价格指数)",
         "CSI500": "中证500(价格指数)"}


def pct(x, d=1):
    return "n/a" if x is None or (isinstance(x, float) and np.isnan(x)) else f"{x * 100:.{d}f}%"


def num(x, d=2):
    return "n/a" if x is None or (isinstance(x, float) and np.isnan(x)) else f"{x:.{d}f}"


def table(metrics: pd.DataFrame, period: str, full: bool = True) -> str:
    cols = ["策略/基准", "起止", "CAGR", "年化波动", "xSharpe", "最大回撤(峰→谷)", "最差整年"]
    if full:
        cols += ["单边年换手", "成本拖累/年", "成交笔数", "平均持仓数", "平均现金占比", "退市持仓次数", "被拦截订单"]
    lines = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for r in RUN_ORDER:
        m = metrics[(metrics["run"] == r) & (metrics["period"] == period)]
        if m.empty:
            continue
        m = m.iloc[0]
        row = [LABEL[r], f"{m['start']}~{m['end']}", pct(m["cagr"]), pct(m["ann_vol"]), num(m["xsharpe"]),
               f"{pct(m['max_drawdown'])} ({m['mdd_peak']}→{m['mdd_trough']})",
               f"{pct(m['worst_full_year'])} ({m['worst_year_label']})" if pd.notna(m["worst_full_year"]) else "n/a"]
        if full:
            isb = pd.notna(m.get("n_trades"))
            row += [pct(m.get("ann_one_way_turnover"), 0) if isb else "-", pct(m.get("cost_drag_ann")) if isb else "-",
                    f"{int(m['n_trades'])}" if isb else "-", num(m.get("mean_holdings"), 1) if isb else "-",
                    pct(m.get("mean_cash_weight")) if isb else "-",
                    f"{int(m['delisted_holdings'])}" if pd.notna(m.get("delisted_holdings")) else "-",
                    f"{int(m['blocked_buy'] + m['blocked_sell'])}" if pd.notna(m.get("blocked_buy")) else "-"]
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)
