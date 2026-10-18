"""CN signal construction: universe-parameterised mirrors of the research_v2 / research_v3 signal glue.

Reused by import (unchanged): find_anchor_session, compute_weekly_momentum, compute_weekly_trend,
select_top_assets (all research_v2.signals), compute_matched_monthly_signals, cap_select and
make_defensive_provider (research_v3.signals_step3), CashYieldCurve (research_v3.cash_yield).

Mirrored because they hardcode the US UNIVERSE: compute_model_signals_for_events and
research_v3.signals_step3.build_weights. Equivalence on US inputs is tested.
"""
from __future__ import annotations

import bisect
from typing import Any, Callable, Mapping, Sequence

import numpy as np
import pandas as pd

from research_v2.contracts import PeriodEvent, SignalFrame
from research_v2.signals import (compute_weekly_momentum, compute_weekly_trend, find_anchor_session,
                                 select_top_assets)
from research_v3.cash_yield import CashYieldCurve
from research_v3.signals_step3 import cap_select


class ShiborCurve(CashYieldCurve):
    """Cash accrual from 3M SHIBOR: simple money-market convention.
    daily accrual factor = 1 + (rate_pct(previous session, forward-filled from earlier dates) / 100) * calendar_days / 365.
    Inherits date lookup (no look-ahead, fails if no earlier value) and accrual_factor from CashYieldCurve;
    only the rate -> yield conversion differs (no discount-rate conversion)."""

    def yield_as_of(self, date: str) -> float:
        return self.pct_as_of(date) / 100.0


def compute_model_signals_cn(bars: Mapping[str, pd.DataFrame], universe: Sequence[str], session_dates: Sequence[str],
                             weekly_events: Sequence[PeriodEvent], model_id: str, momentum_months: int,
                             trend_weeks: int) -> SignalFrame:
    """Mirror of research_v2.signals.compute_model_signals_for_events with `universe` as a parameter."""
    daily_prices: dict[str, dict[str, float]] = {}
    for sym in universe:
        df = bars[sym]
        daily_prices[sym] = dict(zip(df["Date"], df["adj_close"]))

    weekly_closes: dict[str, list[float]] = {sym: [] for sym in universe}
    weekly_sessions: list[str] = []

    rows = []
    lineages = []

    for t_idx, event in enumerate(weekly_events):
        s_t = event.signal_session
        weekly_sessions.append(s_t)

        for sym in universe:
            p = daily_prices[sym].get(s_t, float("nan"))
            weekly_closes[sym].append(p)

        if t_idx < 1:
            continue

        s_prev = weekly_events[t_idx - 1].signal_session

        try:
            anchor_s = find_anchor_session(s_t, momentum_months, session_dates)
        except ValueError:
            continue

        mom_scores: dict[str, float | None] = {}
        trend_passes: dict[str, bool] = {}
        period_lineage: dict[str, Any] = {
            "period_id": event.period_id,
            "signal_session": s_t,
            "feature_cutoff": event.signal_close_ts,
            "latest_input_ts": event.signal_close_ts,
            "symbols": {},
        }

        for sym in universe:
            mom, line = compute_weekly_momentum(s_t, s_prev, anchor_s, daily_prices[sym])
            mom_scores[sym] = mom

            curr_close = weekly_closes[sym][-1]
            hist_closes = weekly_closes[sym]
            passed, sma = compute_weekly_trend(curr_close, hist_closes, trend_weeks)
            trend_passes[sym] = passed

            line.update({
                "current_weekly_close": curr_close,
                "trend_weeks": trend_weeks,
                "sma_k": sma,
                "trend_pass": passed,
            })
            period_lineage["symbols"][sym] = line

        selection = select_top_assets(mom_scores, trend_passes, top_n=3, universe=tuple(universe))

        row = {
            "period_id": event.period_id,
            "signal_session": s_t,
            "execution_session": event.execution_session,
            "execution_open_ts": event.execution_open_ts,
            "status": event.status,
            "eligible_count": len(selection["eligible_symbols"]),
            "eligible_symbols": selection["eligible_symbols"],
            "selected_symbols": selection["selected_symbols"],
            "weights": selection["target_weights"],
            "momentum_scores": mom_scores,
            "trend_passes": trend_passes,
        }
        rows.append(row)
        lineages.append(period_lineage)

    return SignalFrame(model_id=model_id, rows=rows, lineage=lineages)


def build_weights_cn(row: Mapping, *, universe: Sequence[str], cap_groups: Mapping[str, Sequence[str]] | None,
                     slots: str, defensive: Callable[[str], str] | None, top_n: int = 3,
                     def_symbol: str) -> tuple[dict, list[str], float]:
    """Mirror of research_v3.signals_step3.build_weights (sigma/IVOL branch removed: not part of CAP_DEF) with
    `universe` as a parameter. Returns (weights incl. CASH, selected risky symbols, weight placed in def_symbol
    through defensive slots)."""
    universe = tuple(universe)
    sel = select_top_assets(row["momentum_scores"], row["trend_passes"], top_n=top_n, universe=universe)
    eligible = sel["eligible_symbols"]
    selected = cap_select(eligible, cap_groups, top_n) if cap_groups is not None else sel["selected_symbols"]
    k = len(selected)
    weights = {s: 0.0 for s in universe}
    weights["CASH"] = 0.0
    if slots == "renorm":
        risky_total, unfilled = 1.0, 0
        if k == 0:
            weights["CASH"] = 1.0
    elif slots in ("cashslot", "def"):
        risky_total, unfilled = k / top_n, top_n - k
    else:
        raise ValueError(f"unknown slot mode {slots!r}")
    if k:
        w = {s: risky_total / k for s in selected}
        for s, v in w.items():
            weights[s] += v
    def_w = 0.0
    if unfilled:
        slot_w = unfilled / top_n
        if slots == "def" and defensive is not None and defensive(row["signal_session"]) == def_symbol:
            weights[def_symbol] += slot_w
            def_w = slot_w
        else:
            weights["CASH"] += slot_w
    return weights, list(selected), def_w


def cap_def_frame(frame: SignalFrame, model_id: str, *, universe, cap_groups, defensive, def_symbol,
                  top_n: int = 3) -> SignalFrame:
    """Apply CAP + DEF slots to every row of a (matched monthly) base frame; stores def_bond_weight per row."""
    rows = []
    for r in frame.rows:
        w, selected, def_w = build_weights_cn(r, universe=universe, cap_groups=cap_groups, slots="def",
                                              defensive=defensive, top_n=top_n, def_symbol=def_symbol)
        row = dict(r)
        row.update(selected_symbols=selected, weights=w, def_bond_weight=def_w)
        rows.append(row)
    return SignalFrame(model_id=model_id, rows=rows, lineage=frame.lineage)


def time_weighted_weight(dates: Sequence[str], frame: SignalFrame, key=None, symbol: str | None = None) -> float:
    """Mean over `dates` of a held-target quantity from the latest executed COMPLETE signal row
    (execution_session <= date): row[key] or row['weights'][symbol]. 0 before the first execution."""
    by_exec = sorted(
        (r["execution_session"], r.get(key, 0.0) if key else r["weights"].get(symbol, 0.0))
        for r in frame.rows if r.get("status") == "COMPLETE")
    ex = [e for e, _ in by_exec]
    vals = []
    for d in dates:
        i = bisect.bisect_right(ex, d) - 1
        vals.append(by_exec[i][1] if i >= 0 else 0.0)
    return float(np.mean(vals))
