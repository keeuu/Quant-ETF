"""Daily MTM engine for the us_long study.

Mirror of research_v3.cn_transfer.engine_cn.run_backtest_cn (itself a mirror of research_v3.engine.run_backtest_v3),
with three differences:
  1. prices are passed as dicts (exec_prices / close_prices: sym -> {date: price}) so the execution price can be
     the next open (real ETF data) or the close of the execution session (index/rate proxies with a one-session
     lag, as protocol_us_long.json requires);
  2. signal rows may be of kind "exit" (CRASH_WEEKLY_EXIT): sell only the listed holdings at the execution price
     and put the proceeds in the DEF destination (a held symbol or cash). Nothing else is traded. Same
     self-financing cost convention as the ledger: fee = c * gross traded notional;
  3. optional suspension rules (CN secondary run), same semantics as engine_cn (postpone a rebalance to the next
     session on which all needed symbols have prices; newer full rows supersede older pending ones).
With only "full" rows, no suspension rules and exec_prices == the real adj_open it reproduces run_backtest_v3
exactly (tests_v3/test_us_long_engine.py).
"""
from __future__ import annotations

import math
from typing import Any, Mapping, Sequence

import pandas as pd

from research_v2.contracts import DailyNavRecord
from research_v2.engine import EngineError
from research_v2.ledger import execute_rebalance
from research_v2.metrics import compute_backtest_metrics
from research_v3.cash_yield import CashYieldCurve
from research_v3.config import V3Config

EPS = 1e-10
REB_COLS = ["signal_session", "execution_session", "model_id", "cost_bps", "V_pre", "V_post", "fee",
            "solver_residual", "one_way_turnover", "gross_traded_notional", "gross_traded_ratio",
            "cash_before", "cash_after"]


def execute_exit(sig: Mapping, session: str, shares: dict, cash: float, px: Mapping[str, Mapping[str, float]],
                 cost_bps: float, universe: Sequence[str], model_id: str):
    """Sell the symbols in sig["exits"] ({sym: dest}, dest = a symbol or "CASH"); returns (shares, cash, record, sold)."""
    c = cost_bps / 10000.0
    price = {}
    for sym in universe:
        if shares[sym] > EPS:
            p = px[sym].get(session)
            if p is None or not math.isfinite(p) or p <= 0:
                raise EngineError(f"Session {session}: missing exec price for {sym}")
            price[sym] = p
    v_pre = cash + sum(shares[s] * price[s] for s in price)
    to_cash, to_sym, sold = 0.0, {}, []
    new_sh = dict(shares)
    for sym, dest in sig["exits"].items():
        if new_sh.get(sym, 0.0) <= EPS:
            continue
        val = new_sh[sym] * price[sym]
        new_sh[sym] = 0.0
        sold.append(sym)
        if dest == "CASH":
            to_cash += val
        else:
            to_sym[dest] = to_sym.get(dest, 0.0) + val
    s_sym = sum(to_sym.values())
    buy = s_sym * (1.0 - c) / (1.0 + c)            # B + c*(S+B) = S  (self-financing, same as the ledger)
    gross = to_cash + s_sym + buy
    fee = c * gross
    new_cash = cash + to_cash * (1.0 - c)
    for dest, val in to_sym.items():
        p = px[dest].get(session)
        if p is None or not math.isfinite(p) or p <= 0:
            raise EngineError(f"Session {session}: missing exec price for exit destination {dest}")
        new_sh[dest] = new_sh.get(dest, 0.0) + (val * (1.0 - c) / (1.0 + c)) / p
        price.setdefault(dest, p)
    v_post = new_cash + sum(new_sh[s] * price[s] for s in new_sh if new_sh[s] > EPS)
    if abs(v_post - (v_pre - fee)) > 1e-8 * max(1.0, v_pre):
        raise EngineError(f"exit conservation mismatch: {v_post} vs {v_pre - fee}")
    w_before = {s: shares[s] * price[s] / v_pre for s in shares if shares[s] > EPS}
    w_after = {s: new_sh[s] * price[s] / v_post for s in new_sh if new_sh[s] > EPS}
    turn = 0.5 * (abs(new_cash / v_post - cash / v_pre)
                  + sum(abs(w_after.get(s, 0.0) - w_before.get(s, 0.0)) for s in set(w_before) | set(w_after)))
    rec = dict(signal_session=sig["signal_session"], execution_session=session, model_id=model_id, cost_bps=cost_bps,
               V_pre=v_pre, V_post=v_post, fee=fee, solver_residual=0.0, one_way_turnover=turn,
               gross_traded_notional=gross, gross_traded_ratio=gross / v_pre if v_pre > 0 else 0.0,
               cash_before=cash, cash_after=new_cash)
    return new_sh, new_cash, rec, sold


def run_backtest_ul(cal, sessions: Sequence[str], rows: Sequence[Mapping], exec_prices: Mapping, close_prices: Mapping,
                    cfg: V3Config, curve: CashYieldCurve | None, universe: Sequence[str], model_id: str,
                    suspension_rules: bool = False, record_positions: bool = False
                    ) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    """sessions: scored sessions. rows: signal rows (kind "full" default: weights; kind "exit": exits)."""
    if cfg.cash_yield_source == "irx" and curve is None:
        raise EngineError("cash_yield_source='irx' requires a CashYieldCurve")
    initial_nav = cfg.initial_nav
    sessions = list(sessions)
    if not sessions:
        raise EngineError("No trading sessions")
    session_set = set(sessions)
    score_end = sessions[-1]

    by_exec: dict[str, list] = {}
    for sig in rows:
        if sig["execution_session"] <= score_end and sig.get("status") == "COMPLETE" \
                and sig["execution_session"] in session_set:
            by_exec.setdefault(sig["execution_session"], []).append(sig)
    for d, lst in by_exec.items():
        if len(lst) > 1:
            raise EngineError(f"two signal rows execute on {d}")

    queue: list[dict] = []
    postponements: list[dict] = []
    last_close: dict[str, float] = {}
    carried_days = {s: 0 for s in universe}
    cash = float(initial_nav)
    shares = {s: 0.0 for s in universe}
    prev_nav = float(initial_nav)
    nav_rows, reb_rows, exit_log = [], [], []
    pos_rows = []
    eff_cost = cfg.effective_cost_bps

    def do_full(sig, session):
        nonlocal shares, cash
        tw = {k: v for k, v in sig["weights"].items() if k != "CASH"}
        opens = {s: exec_prices[s][session] for s in universe if session in exec_prices[s]}
        shares, cash, _t, rec = execute_rebalance(
            signal_session=sig["signal_session"], execution_session=session, model_id=model_id,
            current_shares=shares, current_cash=cash, execution_open_prices=opens,
            target_weights=tw, cost_bps=eff_cost, universe=universe)
        return vars(rec)

    def do_exit(sig, session):
        nonlocal shares, cash
        shares, cash, rec, sold = execute_exit(sig, session, shares, cash, exec_prices, eff_cost, universe, model_id)
        exit_log.append({"signal_session": sig["signal_session"], "execution_session": session, "sold": ",".join(sold),
                         "dest": ",".join(sorted({sig["exits"][s] for s in sold}))})
        return rec

    def needed_syms(sig):
        if sig.get("kind", "full") == "exit":
            ex = [s for s in sig["exits"] if shares[s] > EPS]
            return ex + [d for s, d in sig["exits"].items() if shares[s] > EPS and d != "CASH"] if ex else []
        tw = sig["weights"]
        return [sy for sy in universe if shares[sy] > EPS or tw.get(sy, 0.0) > EPS]

    for i, session in enumerate(sessions):
        interest = 0.0
        if cfg.cash_yield_source == "irx" and i > 0:
            interest = cash * (curve.accrual_factor(sessions[i - 1], session) - 1.0)
            cash += interest

        rebalance_flag, day_fee = False, 0.0
        if suspension_rules:
            if session in by_exec:
                sig = by_exec[session][0]
                if sig.get("kind", "full") == "full":
                    for q in queue:
                        if q.get("logged"):
                            postponements[q["pidx"]]["actual_execution"] = "DROPPED_SUPERSEDED"
                    queue = []
                queue.append({"sig": sig, "scheduled": session, "logged": False})
            if queue:
                q = queue[0]
                sig = q["sig"]
                miss = [sy for sy in set(needed_syms(sig))
                        if session not in exec_prices[sy] or session not in close_prices[sy]]
                if miss and not q["logged"]:
                    q["pidx"] = len(postponements)
                    postponements.append({"signal_session": sig["signal_session"], "scheduled_execution": q["scheduled"],
                                          "kind": sig.get("kind", "full"), "suspended_symbols": ",".join(sorted(miss)),
                                          "actual_execution": None})
                    q["logged"] = True
                if not miss:
                    rec = do_exit(sig, session) if sig.get("kind", "full") == "exit" else do_full(sig, session)
                    reb_rows.append(rec)
                    rebalance_flag, day_fee = True, rec["fee"]
                    if q["logged"]:
                        postponements[q["pidx"]]["actual_execution"] = session
                    queue.pop(0)
            gross_mv = 0.0
            for sym in universe:
                p = close_prices[sym].get(session)
                if p is not None and math.isfinite(p) and p > 0:
                    last_close[sym] = p
                qv = shares[sym]
                if qv > EPS:
                    if p is None or not math.isfinite(p) or p <= 0:
                        if sym not in last_close:
                            raise EngineError(f"Session {session}: no earlier close to carry for {sym}")
                        p = last_close[sym]
                        carried_days[sym] += 1
                    gross_mv += qv * p
        else:
            if session in by_exec:
                sig = by_exec[session][0]
                rec = do_exit(sig, session) if sig.get("kind", "full") == "exit" else do_full(sig, session)
                reb_rows.append(rec)
                rebalance_flag, day_fee = True, rec["fee"]
            gross_mv = 0.0
            for sym in universe:
                qv = shares[sym]
                if qv > EPS:
                    p = close_prices[sym].get(session)
                    if p is None or not math.isfinite(p) or p <= 0:
                        raise EngineError(f"Session {session}: missing or invalid close for {sym}: {p}")
                    gross_mv += qv * p
        nav = cash + gross_mv
        if nav <= 0:
            raise EngineError(f"Session {session}: NAV wiped out: {nav}")
        ret = (nav / initial_nav - 1.0) if i == 0 else (nav / prev_nav - 1.0)
        prev_nav = nav
        rec_d = vars(DailyNavRecord(date=session, nav=nav, daily_return=ret, cash=cash,
                                    gross_exposure=gross_mv / nav, rebalance_flag=rebalance_flag, fee=day_fee))
        rec_d["cash_interest"] = interest
        nav_rows.append(rec_d)
        if record_positions:
            pos_rows.append({"date": session, **{s: shares[s] for s in universe}, "cash": cash})

    nav_df = pd.DataFrame(nav_rows)
    reb_df = pd.DataFrame(reb_rows) if reb_rows else pd.DataFrame(columns=REB_COLS)
    metrics = compute_backtest_metrics(
        daily_nav=nav_df, rebalances=reb_df, initial_nav=initial_nav,
        first_session_open_utc=cal.session_open(pd.Timestamp(sessions[0])),
        last_session_close_utc=cal.session_close(pd.Timestamp(sessions[-1])))
    metrics["total_cash_interest"] = float(nav_df["cash_interest"].sum())
    metrics["pct_days_all_cash"] = float((nav_df["gross_exposure"] < 1e-9).mean())
    metrics["exit_log"] = exit_log
    if suspension_rules:
        if queue and queue[0].get("logged"):
            postponements[queue[0]["pidx"]]["actual_execution"] = "NOT_EXECUTED_BY_SCORE_END"
        metrics["postponements"] = postponements
        metrics["carried_valuation_days"] = {k: v for k, v in carried_days.items() if v}
    if record_positions:
        metrics["positions"] = pd.DataFrame(pos_rows)
    return nav_df, reb_df, metrics
