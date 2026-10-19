"""Execution engine for stock_mf: T+1, board/date price limits, blocked-order retry, suspension carry-forward,
delisting conversion, protocol costs, lot-based (SMALL) and fractional (LARGE) accounts, SHIBOR cash.

Position representation: v_j "adjusted units"; value = v_j * adjusted price. A buy of n shares at raw open p with
same-day factor F = adj_open/open adds v = n / F, so the position is marked on the total-return adjusted series.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .panel import Panel

STAMP_SWITCH = "2023-08-28"
CHINEXT_SWITCH = "2020-08-24"
MIN_BUY_BUDGET = 1.0      # CNY; smaller buys are skipped (a dust position would never pass the sell test)


@dataclass(frozen=True)
class Costs:
    commission_bp: float = 2.5
    min_commission: float = 0.0       # 0 = NO_MIN, 5 = MIN5
    transfer_bp: float = 0.1          # 0.001%
    slippage_bp: float = 10.0

    @property
    def slip(self):
        return self.slippage_bp / 1e4

    def commission(self, notional: float) -> float:
        if notional <= 0:
            return 0.0
        return max(notional * self.commission_bp / 1e4, self.min_commission)

    def transfer(self, notional: float) -> float:
        return notional * self.transfer_bp / 1e4

    @staticmethod
    def stamp_rate(date: str) -> float:
        return 0.001 if date < STAMP_SWITCH else 0.0005

    def sell_fees(self, notional: float, date: str) -> float:
        return self.commission(notional) + self.transfer(notional) + notional * self.stamp_rate(date)

    def buy_fees(self, notional: float) -> float:
        return self.commission(notional) + self.transfer(notional)


def limit_pct(board: str, date: str, st: bool) -> float:
    if st:
        return 0.05
    if board == "STAR":
        return 0.20
    if board == "CHINEXT":
        return 0.20 if date >= CHINEXT_SWITCH else 0.10
    return 0.10


def _r2(x: float) -> float:
    return math.floor(x * 100.0 + 0.5 + 1e-9) / 100.0


def limit_prices(ref: float, pct: float) -> tuple:
    return _r2(ref * (1.0 + pct)), _r2(ref * (1.0 - pct))


def buy_blocked(open_px: float, ref: float, pct: float, has_bar: bool) -> bool:
    if not has_bar:
        return True
    if not np.isfinite(ref):
        return False
    up, _ = limit_prices(ref, pct)
    return open_px >= up - 1e-9


def sell_blocked(open_px: float, ref: float, pct: float, has_bar: bool) -> bool:
    if not has_bar:
        return True
    if not np.isfinite(ref):
        return False
    _, dn = limit_prices(ref, pct)
    return open_px <= dn + 1e-9


def buy_quantity(budget: float, price_exec: float, costs: Costs, lot: int | None) -> float:
    """Largest quantity whose cost (notional + commission incl. minimum + transfer) fits in `budget`.
    lot=None -> fractional. Exact (no search): both the proportional and the minimum-commission branches are solved."""
    if budget <= 0 or price_exec <= 0:
        return 0.0
    c, tr = costs.commission_bp / 1e4, costs.transfer_bp / 1e4
    n1 = budget / (price_exec * (1.0 + c + tr))
    n2 = (budget - costs.min_commission) / (price_exec * (1.0 + tr)) if costs.min_commission > 0 else n1
    n = min(n1, n2)
    if n <= 0:
        return 0.0
    if lot:
        return float(int(n / lot + 1e-9) * lot)
    return n


@dataclass
class SimResult:
    nav: pd.DataFrame
    trades: list
    counters: dict
    held_end: dict = field(default_factory=dict)


def simulate(p: Panel, accrual: np.ndarray, start: int, end: int, signals, targets_fn, capital: float, costs: Costs,
             lots: bool, record_trades: bool = True) -> SimResult:
    """Run sessions start..end (inclusive row indices). `accrual[i]` = cash growth factor from session i-1 to i.
    `signals`: set of rows at whose close targets_fn(i, held{j: value}, pv) -> {j: target CNY value or None (keep)} is
    called; held names absent from the mapping get target 0. Orders execute at the next open(s)."""
    S, N = p.close.shape
    v = np.zeros(N)
    buy_row = np.full(N, -1)
    cash = float(capital)
    pending: dict = {}
    trades: list = []
    cnt = dict(fills_buy=0, fills_sell=0, blocked_buy=0, blocked_sell=0, delisted_holdings=0, lot_zero=0, fees=0.0,
               slippage_cost=0.0, buy_notional=0.0, sell_notional=0.0, signals=0, t1_blocked=0)
    navs, cashs, nholds, dates = [], [], [], []
    daily = {k: [] for k in ("buy_notional", "sell_notional", "fees", "slippage", "fills", "blocked")}
    prev_tot = dict(buy_notional=0.0, sell_notional=0.0, fees=0.0, slippage=0.0, fills=0, blocked=0)
    signals = set(signals)
    sess = p.sessions
    for i in range(start, end + 1):
        date = sess[i]
        if i > start:
            cash *= accrual[i]
        # --- delisting conversion: holdings of a stock that stopped trading permanently
        if p.delisted.any():
            dl = np.where(p.delisted & (p.last_bar_idx < i) & (v > 0))[0]
            for j in dl:
                cash += v[j] * p.adj_close_ff[p.last_bar_idx[j], j]
                v[j] = 0.0
                cnt["delisted_holdings"] += 1
                pending.pop(j, None)
        # --- execute pending orders at the open
        if pending:
            ops = p.adj_open[i]
            prev_ff = p.adj_close_ff[max(i - 1, 0)]
            sells = [j for j, tgt in pending.items() if v[j] > 0 and (tgt <= 0 or tgt < v[j] * prev_ff[j] - 1e-9)]
            buys = [j for j, tgt in pending.items() if j not in sells and tgt is not None]
            done = []
            for j in sells:
                hb = bool(p.has_bar[i, j])
                pct = limit_pct(p.board[j], date, bool(p.st[i - 1, j])) if i > 0 else 0.1
                if buy_row[j] >= i:
                    cnt["t1_blocked"] += 1
                    cnt["blocked_sell"] += 1
                    continue
                if sell_blocked(p.open[i, j], p.ref_close[i, j], pct, hb):
                    cnt["blocked_sell"] += 1
                    continue
                cur = v[j] * ops[j]
                tgt = pending[j]
                if tgt > 0 and cur <= tgt + 1e-9:       # drifted below the (partial-sell) target since the signal: nothing to sell
                    done.append(j)
                    continue
                sell_val = cur if tgt <= 0 else cur - tgt
                frac = min(1.0, sell_val / cur)
                notional_ref = cur * frac
                notional = notional_ref * (1.0 - costs.slip)
                fee = costs.sell_fees(notional, date)
                cash += notional - fee
                v[j] *= (1.0 - frac) if frac < 1.0 - 1e-12 else 0.0
                if v[j] < 1e-12:
                    v[j] = 0.0
                cnt["fills_sell"] += 1
                cnt["fees"] += fee
                cnt["slippage_cost"] += notional_ref - notional
                cnt["sell_notional"] += notional
                done.append(j)
                if record_trades:
                    trades.append(dict(date=date, code=p.codes[j], side="S", shares=notional_ref / p.open[i, j],
                                       price=p.open[i, j] * (1 - costs.slip), notional=notional, fee=fee))
            for j in buys:
                hb = bool(p.has_bar[i, j])
                pct = limit_pct(p.board[j], date, bool(p.st[i - 1, j])) if i > 0 else 0.1
                if buy_blocked(p.open[i, j], p.ref_close[i, j], pct, hb):
                    cnt["blocked_buy"] += 1
                    continue
                cur = v[j] * ops[j]
                delta = pending[j] - cur
                if delta <= 1e-9:
                    done.append(j)
                    continue
                px = p.open[i, j] * (1.0 + costs.slip)
                budget = min(delta, cash)
                done.append(j)
                if budget < MIN_BUY_BUDGET:            # no dust positions when cash is exhausted
                    cnt["lot_zero"] += 1
                    continue
                n = buy_quantity(budget, px, costs, int(p.lot[j]) if lots else None)
                if n <= 0:
                    cnt["lot_zero"] += 1
                    continue
                notional = n * px
                fee = costs.buy_fees(notional)
                cash -= notional + fee
                v[j] += n * p.open[i, j] / ops[j]
                buy_row[j] = i
                cnt["fills_buy"] += 1
                cnt["fees"] += fee
                cnt["slippage_cost"] += n * p.open[i, j] * costs.slip
                cnt["buy_notional"] += notional
                if record_trades:
                    trades.append(dict(date=date, code=p.codes[j], side="B", shares=n, price=px, notional=notional, fee=fee))
            for j in done:
                pending.pop(j, None)
        # --- mark to market at the close (carried-forward adjusted close)
        hold = v > 0
        pv = cash + float(np.dot(v[hold], p.adj_close_ff[i][hold])) if hold.any() else cash
        tot = dict(buy_notional=cnt["buy_notional"], sell_notional=cnt["sell_notional"], fees=cnt["fees"],
                   slippage=cnt["slippage_cost"], fills=cnt["fills_buy"] + cnt["fills_sell"],
                   blocked=cnt["blocked_buy"] + cnt["blocked_sell"])
        for k_ in daily:
            daily[k_].append(tot[k_] - prev_tot[k_])
        prev_tot = tot
        navs.append(pv)
        cashs.append(cash)
        nholds.append(int(hold.sum()))
        dates.append(date)
        # --- signal at this close: replaces all unfilled orders
        if i in signals and i < end:
            cnt["signals"] += 1
            held = {int(j): float(v[j] * p.adj_close_ff[i, j]) for j in np.where(hold)[0]}
            tg = targets_fn(i, held, pv)
            pending = {}
            for j in held:
                if j not in tg:
                    pending[j] = 0.0
            for j, val in tg.items():
                if val is not None:
                    pending[int(j)] = float(val)
    nav = pd.DataFrame({"date": dates, "nav": navs, "cash": cashs, "n_hold": nholds, **daily})
    nav["ret"] = nav["nav"].pct_change()
    nav.loc[0, "ret"] = nav["nav"].iloc[0] / capital - 1.0
    return SimResult(nav, trades, cnt, {int(j): float(v[j]) for j in np.where(v > 0)[0]})
