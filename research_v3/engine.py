"""research_v3 daily MTM engine: research_v2 run_backtest + cash interest + slippage.

This is a line-for-line mirror of research_v2.engine.run_backtest (research_v2 is read-only),
with exactly two additions:
  1. optional cash interest credited at the start of each session after the first
     (see cash_yield.py for conventions), BEFORE the open rebalance;
  2. the ledger cost is cfg.cost_bps + cfg.slippage_bps (effective per-side cost on traded notional).
With cash_yield_source="none" and slippage_bps=0 it reproduces v2 exactly (tested).
"""
from __future__ import annotations

import math
from typing import Any

import pandas as pd

from research_v2.calendar import get_trading_calendar
from research_v2.contracts import DailyNavRecord, DatasetView, SignalFrame, UNIVERSE
from research_v2.engine import EngineError
from research_v2.ledger import execute_rebalance
from research_v2.metrics import compute_backtest_metrics

from .cash_yield import CashYieldCurve
from .config import V3Config
from .guards import assert_date_allowed, assert_partition_allowed


def run_backtest_v3(
    dataset_view: DatasetView,
    signals: SignalFrame,
    cfg: V3Config,
    curve: CashYieldCurve | None = None,
    universe=UNIVERSE,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    """Returns (daily_nav_df [+cash_interest column], rebalance_df, metrics)."""
    assert_partition_allowed(dataset_view.partition)
    assert_date_allowed(dataset_view.score_end)
    if cfg.cash_yield_source == "irx" and curve is None:
        raise EngineError("cash_yield_source='irx' requires a CashYieldCurve")
    initial_nav = cfg.initial_nav
    if initial_nav <= 0:
        raise EngineError(f"Initial capital must be positive, got {initial_nav}")

    cal = get_trading_calendar()
    sessions = [s.strftime("%Y-%m-%d") for s in
                cal.sessions_in_range(pd.Timestamp(dataset_view.score_start), pd.Timestamp(dataset_view.score_end))]
    if not sessions:
        raise EngineError("No trading sessions in partition")
    session_set = set(sessions)

    execution_signals: dict[str, dict[str, Any]] = {}
    for sig in signals.rows:
        if sig["execution_session"] <= dataset_view.score_end and sig.get("status") == "COMPLETE" \
                and sig["execution_session"] in session_set:
            execution_signals[sig["execution_session"]] = sig

    open_prices: dict[str, dict[str, float]] = {s: {} for s in universe}
    close_prices: dict[str, dict[str, float]] = {s: {} for s in universe}
    for sym in universe:
        for _, row in dataset_view.bars[sym].iterrows():
            d = str(row["Date"])
            o, c = row.get("adj_open"), row.get("adj_close")
            if o is not None and not math.isnan(o):
                open_prices[sym][d] = float(o)
            if c is not None and not math.isnan(c):
                close_prices[sym][d] = float(c)

    cash = float(initial_nav)
    shares = {s: 0.0 for s in universe}
    prev_nav = float(initial_nav)
    nav_rows: list[dict[str, Any]] = []
    reb_rows: list[dict[str, Any]] = []
    eff_cost = cfg.effective_cost_bps

    for i, session in enumerate(sessions):
        interest = 0.0
        if cfg.cash_yield_source == "irx" and i > 0:
            interest = cash * (curve.accrual_factor(sessions[i - 1], session) - 1.0)
            cash += interest

        if session in execution_signals:
            sig = execution_signals[session]
            tw = {k: v for k, v in sig["weights"].items() if k != "CASH"}
            opens = {s: open_prices[s][session] for s in universe if session in open_prices[s]}
            shares, cash, _trades, rec = execute_rebalance(
                signal_session=sig["signal_session"], execution_session=session, model_id=signals.model_id,
                current_shares=shares, current_cash=cash, execution_open_prices=opens,
                target_weights=tw, cost_bps=eff_cost, universe=universe,
            )
            reb_rows.append(vars(rec))
            rebalance_flag, day_fee = True, rec.fee
        else:
            rebalance_flag, day_fee = False, 0.0

        gross_mv = 0.0
        for sym in universe:
            q = shares[sym]
            if q > 1e-10:
                p = close_prices[sym].get(session)
                if p is None or math.isnan(p) or p <= 0:
                    raise EngineError(f"Session {session}: missing or invalid close for {sym}: {p}")
                gross_mv += q * p
        nav = cash + gross_mv
        if nav <= 0:
            raise EngineError(f"Session {session}: NAV wiped out: {nav}")
        ret = (nav / initial_nav - 1.0) if i == 0 else (nav / prev_nav - 1.0)
        prev_nav = nav
        rec_d = vars(DailyNavRecord(date=session, nav=nav, daily_return=ret, cash=cash,
                                    gross_exposure=gross_mv / nav, rebalance_flag=rebalance_flag, fee=day_fee))
        rec_d["cash_interest"] = interest
        nav_rows.append(rec_d)

    nav_df = pd.DataFrame(nav_rows)
    reb_df = pd.DataFrame(reb_rows) if reb_rows else pd.DataFrame(
        columns=["signal_session", "execution_session", "model_id", "cost_bps", "V_pre", "V_post", "fee",
                 "solver_residual", "one_way_turnover", "gross_traded_notional", "gross_traded_ratio",
                 "cash_before", "cash_after"])
    metrics = compute_backtest_metrics(
        daily_nav=nav_df, rebalances=reb_df, initial_nav=initial_nav,
        first_session_open_utc=cal.session_open(pd.Timestamp(sessions[0])),
        last_session_close_utc=cal.session_close(pd.Timestamp(sessions[-1])),
    )
    metrics["total_cash_interest"] = float(nav_df["cash_interest"].sum())
    metrics["pct_days_all_cash"] = float((nav_df["gross_exposure"] < 1e-9).mean())
    return nav_df, reb_df, metrics
