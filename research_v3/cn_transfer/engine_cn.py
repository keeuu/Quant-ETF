"""CN daily MTM engine (amendment-1 suspension rules behind `suspension_rules=True`, default off = fail-closed).

Amendment 1 (protocol_cn_transfer_amendment1.json), active only with suspension_rules=True:
  * valuation: a held symbol with no bar on a session is valued at its last available adjusted close (carry-forward);
    no later price is ever used.
  * trading: if any held or target symbol lacks a bar (open and close) on a rebalance's execution session, the whole
    rebalance (same weights) is postponed to the next session on which all of them have bars; every postponement is
    logged in metrics["postponements"]. A pending rebalance superseded by the next scheduled one is logged as dropped.
CN daily MTM engine: line-for-line mirror of research_v3.engine.run_backtest_v3 with the XNYS calendar and the
US holdout guards replaced by an explicit calendar argument (the holdout guard is a US-data control and does not
apply to CN data; the US code path is untouched).

With cal = XNYS calendar and universe = research_v2 UNIVERSE it reproduces run_backtest_v3 on train/validation
data exactly (tested). SHIBOR/any CashYieldCurve subclass supplies the cash accrual.
"""
from __future__ import annotations

import math
from typing import Any, Sequence

import pandas as pd

from research_v2.contracts import DailyNavRecord, SignalFrame
from research_v2.engine import EngineError
from research_v2.ledger import execute_rebalance
from research_v2.metrics import compute_backtest_metrics
from research_v3.cash_yield import CashYieldCurve
from research_v3.config import V3Config


def run_backtest_cn(cal, bars: dict[str, pd.DataFrame], score_start: str, score_end: str, signals: SignalFrame,
                    cfg: V3Config, curve: CashYieldCurve | None, universe: Sequence[str],
                    suspension_rules: bool = False) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    """Returns (daily_nav_df [+cash_interest column], rebalance_df, metrics)."""
    if cfg.cash_yield_source == "irx" and curve is None:
        raise EngineError("cash_yield_source='irx' requires a CashYieldCurve")
    initial_nav = cfg.initial_nav
    if initial_nav <= 0:
        raise EngineError(f"Initial capital must be positive, got {initial_nav}")

    sessions = [s.strftime("%Y-%m-%d") for s in
                cal.sessions_in_range(pd.Timestamp(score_start), pd.Timestamp(score_end))]
    if not sessions:
        raise EngineError("No trading sessions in partition")
    session_set = set(sessions)

    execution_signals: dict[str, dict[str, Any]] = {}
    for sig in signals.rows:
        if sig["execution_session"] <= score_end and sig.get("status") == "COMPLETE" \
                and sig["execution_session"] in session_set:
            execution_signals[sig["execution_session"]] = sig

    open_prices: dict[str, dict[str, float]] = {s: {} for s in universe}
    close_prices: dict[str, dict[str, float]] = {s: {} for s in universe}
    for sym in universe:
        for _, row in bars[sym].iterrows():
            d = str(row["Date"])
            o, c = row.get("adj_open"), row.get("adj_close")
            if o is not None and not math.isnan(o):
                open_prices[sym][d] = float(o)
            if c is not None and not math.isnan(c):
                close_prices[sym][d] = float(c)

    pending = None
    postponements: list[dict[str, Any]] = []
    last_close: dict[str, float] = {}
    carried_days: dict[str, int] = {sy: 0 for sy in universe}
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

        if suspension_rules:
            if session in execution_signals:
                if pending is not None:
                    postponements[-1]["actual_execution"] = "DROPPED_SUPERSEDED"
                pending = {"sig": execution_signals[session], "scheduled": session, "logged": False}
            do_exec = False
            if pending is not None:
                sig = pending["sig"]
                tw = {k: v for k, v in sig["weights"].items() if k != "CASH"}
                needed = [sy for sy in universe if shares[sy] > 1e-10 or tw.get(sy, 0.0) > 1e-10]
                missing = [sy for sy in needed if session not in open_prices[sy] or session not in close_prices[sy]]
                if missing and not pending["logged"]:
                    postponements.append({"signal_session": sig["signal_session"], "scheduled_execution": session,
                                          "suspended_symbols": ",".join(missing), "actual_execution": None})
                    pending["logged"] = True
                do_exec = not missing
            if do_exec:
                opens = {sy: open_prices[sy][session] for sy in universe if session in open_prices[sy]}
                shares, cash, _trades, rec = execute_rebalance(
                    signal_session=sig["signal_session"], execution_session=session, model_id=signals.model_id,
                    current_shares=shares, current_cash=cash, execution_open_prices=opens,
                    target_weights=tw, cost_bps=eff_cost, universe=universe)
                reb_rows.append(vars(rec))
                rebalance_flag, day_fee = True, rec.fee
                if pending["logged"]:
                    postponements[-1]["actual_execution"] = session
                pending = None
            else:
                rebalance_flag, day_fee = False, 0.0
            gross_mv = 0.0
            for sym in universe:
                p = close_prices[sym].get(session)
                if p is not None and not math.isnan(p) and p > 0:
                    last_close[sym] = p
                q = shares[sym]
                if q > 1e-10:
                    if p is None or math.isnan(p) or p <= 0:
                        if sym not in last_close:
                            raise EngineError(f"Session {session}: no earlier close to carry for {sym}")
                        p = last_close[sym]          # suspension valuation: last available adjusted close
                        carried_days[sym] += 1
                    gross_mv += q * p
        else:
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
    if suspension_rules:
        if pending is not None:
            postponements[-1]["actual_execution"] = "NOT_EXECUTED_BY_SCORE_END"
        metrics["postponements"] = postponements
        metrics["carried_valuation_days"] = {k: v for k, v in carried_days.items() if v}
    metrics["pct_days_all_cash"] = float((nav_df["gross_exposure"] < 1e-9).mean())
    return nav_df, reb_df, metrics
