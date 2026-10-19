"""Weekly momentum, trend filter, and deterministic asset selection for Research V2.

Strictly implements pre-registered definitions:
- Natural calendar-month lookbacks (6M, 9M, 12M), NOT 26/39/52 weeks.
- Strict one-complete-week skip: numerator = previous complete week close s_(t-1).
- Denominator = trading day on or immediately before s_t - DateOffset(months=L).
- Complete K-week SMA trend filter (36, 40, 44 weeks) including current week.
- Strict '>' operator for trend filter (equal is a fail; no momentum > 0 filter).
- Top 3 equal weighting with universe-order deterministic tie-break.
"""
from __future__ import annotations

import math
from typing import Any, Mapping, Sequence
import numpy as np
import pandas as pd

from .calendar import parse_as_of
from .contracts import PeriodEvent, SignalFrame, UNIVERSE

UNIVERSE_TIE_BREAK = {sym: idx for idx, sym in enumerate(UNIVERSE)}


def find_anchor_session(
    signal_session: str,
    months: int,
    session_dates: Sequence[str],
) -> str:
    """Find the latest trading session on or before signal_session - DateOffset(months=L).

    M is natural calendar month, not a weekly approximation.
    """
    s_t = pd.Timestamp(signal_session)
    target_dt = s_t - pd.DateOffset(months=months)
    target_str = target_dt.strftime("%Y-%m-%d")

    # Binary search for the rightmost session <= target_str
    # session_dates is assumed sorted ascending
    idx = np.searchsorted(session_dates, target_str, side="right") - 1
    if idx < 0:
        raise ValueError(f"No trading session on or before anchor date {target_str} (months={months})")
    return session_dates[idx]


def compute_weekly_momentum(
    signal_session: str,
    prev_week_session: str,
    anchor_session: str,
    daily_adj_close: Mapping[str, float],
) -> tuple[float | None, dict[str, Any]]:
    """Compute one-week-skipped natural-month momentum for a single asset.

    Formula:
        momentum_L(t) = AdjClose[s_(t-1)] / AdjClose[a_L(t)] - 1.0

    Returns:
        (momentum, lineage_record)
    """
    num_price = daily_adj_close.get(prev_week_session)
    den_price = daily_adj_close.get(anchor_session)

    lineage = {
        "signal_session": signal_session,
        "numerator_date": prev_week_session,
        "numerator_price": num_price,
        "denominator_date": anchor_session,
        "denominator_price": den_price,
        "momentum": None,
    }

    if num_price is None or den_price is None:
        return None, lineage

    if not math.isfinite(num_price) or not math.isfinite(den_price) or den_price <= 0 or num_price <= 0:
        return None, lineage

    mom = (num_price / den_price) - 1.0
    lineage["momentum"] = mom
    return mom, lineage


def compute_weekly_trend(
    current_weekly_close: float,
    past_k_weekly_closes: Sequence[float],
    window_weeks: int,
) -> tuple[bool, float | None]:
    """Compute K-week SMA trend filter and evaluate strict '>' condition.

    Must include current completed week and require exact K complete observations.
    past_k_weekly_closes should contain the last K observations ending with current_weekly_close.

    Returns:
        (trend_passed, sma_value)
    """
    if len(past_k_weekly_closes) < window_weeks:
        return False, None

    window = past_k_weekly_closes[-window_weeks:]
    if any(p is None or not math.isfinite(p) or p <= 0 for p in window):
        return False, None

    sma = float(np.mean(window))
    # Strict '>' operator: equal is a fail
    passed = bool(current_weekly_close > sma)
    return passed, sma


def select_top_assets(
    momentum_scores: Mapping[str, float | None],
    trend_passed: Mapping[str, bool],
    top_n: int = 3,
    universe: Sequence[str] = UNIVERSE,
) -> dict[str, Any]:
    """Rank qualified assets and allocate equal weights among Top N.

    Order of operations:
    1. Filter by trend_passed == True and finite momentum.
    2. Sort by momentum descending, breaking ties by fixed Universe order.
    3. Top N equal weighting:
       - 0 qualified: 100% CASH
       - 1 qualified: 100% in asset
       - 2 qualified: 50% / 50%
       - >=3 qualified: 1/3 each in Top 3, rest 0
    """
    # 1. Filter eligible
    eligible = []
    for sym in universe:
        mom = momentum_scores.get(sym)
        passed = bool(trend_passed.get(sym, False))
        if passed and mom is not None and math.isfinite(mom):
            eligible.append(sym)

    # 2. Sort by momentum descending with deterministic tie-break
    # For identical momentum values, smaller universe index wins
    eligible.sort(key=lambda s: (-float(momentum_scores[s]), UNIVERSE_TIE_BREAK.get(s, 999)))

    # 3. Weights calculation
    selected = eligible[:top_n]
    weights: dict[str, float] = {sym: 0.0 for sym in universe}
    weights["CASH"] = 0.0

    num_sel = len(selected)
    if num_sel == 0:
        weights["CASH"] = 1.0
    else:
        alloc = 1.0 / float(num_sel)
        for sym in selected:
            weights[sym] = alloc

    rankings: dict[str, int | None] = {}
    for r_idx, sym in enumerate(eligible, start=1):
        rankings[sym] = r_idx

    return {
        "eligible_symbols": eligible,
        "selected_symbols": selected,
        "target_weights": weights,
        "rankings": rankings,
    }


def compute_model_signals_for_events(
    bars: Mapping[str, pd.DataFrame],
    session_dates: Sequence[str],
    weekly_events: Sequence[PeriodEvent],
    model_id: str,
    momentum_months: int,
    trend_weeks: int,
) -> SignalFrame:
    """Compute complete signal time series and lineages for a specified model.

    Evaluates momentum, trend, selection, and allocations for each weekly event.
    """
    # Pre-index daily prices by session_date for O(1) lookup
    daily_prices: dict[str, dict[str, float]] = {}
    for sym in UNIVERSE:
        df = bars[sym]
        daily_prices[sym] = dict(zip(df["Date"], df["adj_close"]))

    # Map weekly session closes for all assets
    weekly_closes: dict[str, list[float]] = {sym: [] for sym in UNIVERSE}
    weekly_sessions: list[str] = []

    rows = []
    lineages = []

    for t_idx, event in enumerate(weekly_events):
        s_t = event.signal_session
        weekly_sessions.append(s_t)

        # Update running weekly closes for each asset
        for sym in UNIVERSE:
            p = daily_prices[sym].get(s_t, float("nan"))
            weekly_closes[sym].append(p)

        # Cannot compute 1-week skipped momentum on the very first week (no t-1)
        if t_idx < 1:
            continue

        s_prev = weekly_events[t_idx - 1].signal_session

        # Find anchor session on or before s_t - DateOffset(months=L)
        try:
            anchor_s = find_anchor_session(s_t, momentum_months, session_dates)
        except ValueError:
            # Insufficient history for anchor
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

        for sym in UNIVERSE:
            # Momentum
            mom, line = compute_weekly_momentum(s_t, s_prev, anchor_s, daily_prices[sym])
            mom_scores[sym] = mom

            # Trend
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

        # Portfolio selection
        selection = select_top_assets(mom_scores, trend_passes, top_n=3, universe=UNIVERSE)

        # Record signal row
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

    return SignalFrame(
        model_id=model_id,
        rows=rows,
        lineage=lineages,
    )


def compute_matched_monthly_signals(
    weekly_signals: SignalFrame,
    monthly_events: Sequence[PeriodEvent],
    monthly_model_id: str,
) -> SignalFrame:
    """Map the matched monthly cadence onto previously computed weekly signals.

    Must NOT recalculate momentum or trend. It must sample from the already computed
    weekly signals according to the source_signal_session linkage.
    """
    # Index weekly signals by signal_session
    weekly_rows_by_session = {row["signal_session"]: row for row in weekly_signals.rows}
    weekly_lineage_by_session = {lin["signal_session"]: lin for lin in weekly_signals.lineage}

    rows = []
    lineages = []

    for event in monthly_events:
        source_session = event.source_signal_session
        if not source_session or source_session not in weekly_rows_by_session:
            continue

        w_row = weekly_rows_by_session[source_session]
        w_lin = weekly_lineage_by_session[source_session]

        # Copy the exact weekly decision but assign it to the monthly event scheduling
        m_row = dict(w_row)
        m_row["period_id"] = event.period_id
        m_row["signal_session"] = event.signal_session  # The month-end date
        m_row["execution_session"] = event.execution_session
        m_row["execution_open_ts"] = event.execution_open_ts
        m_row["status"] = event.status
        # We explicitly retain the original 'weights', 'selected_symbols', 'momentum_scores', etc.
        rows.append(m_row)

        m_lin = dict(w_lin)
        m_lin["period_id"] = event.period_id
        m_lin["signal_session"] = event.signal_session
        m_lin["feature_cutoff"] = event.feature_cutoff
        # We also keep the original 'latest_input_ts' and 'symbols' from the weekly signal
        lineages.append(m_lin)

    return SignalFrame(
        model_id=monthly_model_id,
        rows=rows,
        lineage=lineages,
    )
