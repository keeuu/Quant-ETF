"""Wrappers around the (unmodified) baseline CN engine for the Part B variants.

* execution lag   : shift every signal row's execution_session k sessions later; the baseline engine
                    (run_backtest_cn, suspension_rules=True) is called as is.
* lot execution   : temporarily replaces `engine_cn.execute_rebalance` (module-level name used by run_backtest_cn) by a
                    whole-lot executor with min-commission; the baseline source file is not touched.
At default settings (lag=0, no lots) run_variant delegates straight to run_backtest_cn, so it reproduces the baseline
NAV bit for bit (tested).
"""
from __future__ import annotations

import contextlib
import math
from dataclasses import dataclass
from typing import Sequence

import pandas as pd

from research_v2.contracts import RebalanceRecord, SignalFrame
from research_v3.config import V3Config

from .. import engine_cn
from ..engine_cn import run_backtest_cn


# ------------------------------------------------------------------ execution lag
def apply_execution_lag(frame: SignalFrame, sessions: Sequence[str], k: int) -> SignalFrame:
    """Rows keep their signal; execution_session moves k sessions after the normal execution session.
    Rows whose shifted session falls beyond the last session are dropped. k=0 returns the frame unchanged."""
    if k < 0:
        raise ValueError("lag must be >= 0")
    if k == 0:
        return frame
    pos = {s: i for i, s in enumerate(sessions)}
    rows = []
    for r in frame.rows:
        ex = r["execution_session"]
        if ex not in pos:           # outside the session list (e.g. before the scoring window): leave as is
            rows.append(r)
            continue
        j = pos[ex] + k
        if j >= len(sessions):
            continue
        nr = dict(r)
        nr["execution_session"] = sessions[j]
        if nr["execution_session"] <= nr["signal_session"]:
            raise ValueError("lagged execution is not strictly after the signal session")
        rows.append(nr)
    return SignalFrame(model_id=frame.model_id, rows=rows, lineage=frame.lineage)


# ------------------------------------------------------------------ lot execution with min commission
@dataclass(frozen=True)
class LotCosts:
    lot: int = 100
    commission_rate: float = 2.5e-4       # 2.5 bp of trade value
    min_commission: float = 5.0           # CNY per trade
    slippage_bps: float = 5.0             # per side, proportional


def trade_cost(value: float, c: LotCosts) -> float:
    """Cost of one trade of absolute value `value` (0 if no trade)."""
    if value <= 0:
        return 0.0
    return max(c.min_commission, c.commission_rate * value) + c.slippage_bps / 1e4 * value


def lot_rebalance(current_shares, cash, opens, weights, universe, c: LotCosts):
    """Whole-lot rebalance at the open. Targets floor(w * V_pre / (open*lot)) lots; leftover stays in cash.
    If fees would drive cash negative, one lot at a time is removed from the largest buy until feasible.
    Returns (new_shares, new_cash, fee, V_pre)."""
    V_pre = cash + sum(current_shares.get(s, 0.0) * opens[s] for s in universe if current_shares.get(s, 0.0) > 0)
    target = {}
    for s in universe:
        w = weights.get(s, 0.0)
        if w > 1e-10:
            target[s] = math.floor(w * V_pre / (opens[s] * c.lot) + 1e-12) * c.lot
        else:
            target[s] = 0
    def settle(tgt):
        cash_after, fee = cash, 0.0
        for s in universe:
            d = tgt[s] - current_shares.get(s, 0.0)
            if abs(d) < 1e-9:
                continue
            val = abs(d) * opens[s]
            cost = trade_cost(val, c)
            fee += cost
            cash_after += (-val if d > 0 else val) - cost
        return cash_after, fee
    cash_after, fee = settle(target)
    guard = 0
    while cash_after < -1e-9:
        buys = [(target[s] - current_shares.get(s, 0.0)) * opens[s] for s in universe]
        i = max(range(len(buys)), key=lambda j: buys[j])
        if buys[i] <= 0:
            raise ValueError("cannot fund rebalance")
        target[universe[i]] -= c.lot
        cash_after, fee = settle(target)
        guard += 1
        if guard > 10000:
            raise ValueError("lot reduction did not converge")
    return {s: float(target[s]) for s in universe}, cash_after, fee, V_pre


def make_lot_executor(c: LotCosts):
    def execute(signal_session, execution_session, model_id, current_shares, current_cash, execution_open_prices,
                target_weights, cost_bps, universe):
        new_shares, new_cash, fee, V_pre = lot_rebalance(current_shares, current_cash, execution_open_prices,
                                                         target_weights, universe, c)
        V_post = new_cash + sum(new_shares[s] * execution_open_prices[s] for s in universe if new_shares[s] > 0)
        gross = sum(abs(new_shares[s] - current_shares.get(s, 0.0)) * execution_open_prices[s] for s in universe
                    if s in execution_open_prices)
        w_pre_cash = current_cash / V_pre if V_pre > 0 else 1.0
        w_post_cash = new_cash / V_post if V_post > 0 else 1.0
        comp = abs(w_post_cash - w_pre_cash)
        for s in universe:
            if s in execution_open_prices:
                comp += abs(new_shares[s] * execution_open_prices[s] / V_post
                            - current_shares.get(s, 0.0) * execution_open_prices[s] / V_pre)
        rec = RebalanceRecord(signal_session=signal_session, execution_session=execution_session, model_id=model_id,
                              cost_bps=cost_bps, V_pre=V_pre, V_post=V_post, fee=fee, solver_residual=0.0,
                              one_way_turnover=0.5 * comp, gross_traded_notional=gross,
                              gross_traded_ratio=gross / V_pre if V_pre > 0 else 0.0,
                              cash_before=current_cash, cash_after=new_cash)
        return new_shares, new_cash, [], rec
    return execute


@contextlib.contextmanager
def patched_executor(executor):
    old = engine_cn.execute_rebalance
    engine_cn.execute_rebalance = executor
    try:
        yield
    finally:
        engine_cn.execute_rebalance = old


def run_variant(cal, bars, score_start: str, score_end: str, frame: SignalFrame, cfg: V3Config, curve, universe,
                sessions: Sequence[str], lag: int = 0, lots: LotCosts | None = None):
    """Same return as run_backtest_cn (suspension_rules=True). Defaults reproduce the baseline exactly."""
    fr = apply_execution_lag(frame, sessions, lag)
    if lots is None:
        return run_backtest_cn(cal, bars, score_start, score_end, fr, cfg, curve, universe, suspension_rules=True)
    with patched_executor(make_lot_executor(lots)):
        return run_backtest_cn(cal, bars, score_start, score_end, fr, cfg, curve, universe, suspension_rules=True)
