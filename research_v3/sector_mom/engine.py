"""Daily mark-to-market engine for the sector_mom strategies.

Model (protocol execution_model): signals at the close, orders at the NEXT session open; T+1 (no sale on the
session of the latest purchase); a buy is blocked if open >= prev_close*(1+limit) (limit 10%, 20% on ChiNext/STAR
tracked ETFs), a sale is blocked if open <= prev_close*(1-limit); blocked orders are retried at every following open
until filled or superseded by the next weekly/monthly rebalance. Costs are charged on traded notional
((commission+slippage) bp per side; lot mode adds a 5 CNY minimum commission). Cash accrues SHIBOR 3M (previous
session's rate, calendar-day simple interest) via the cn_transfer ShiborCurve.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from . import guard
from .panel import Panel

EPS = 1e-9
TICK_TOL = 0.0005


@dataclass(frozen=True)
class Cost:
    commission_bp: float = 2.5
    slippage_bp: float = 10.0

    @property
    def bp(self) -> float:
        return self.commission_bp + self.slippage_bp


PRIMARY = Cost(2.5, 10.0)
SENS = Cost(2.5, 20.0)
ZERO = Cost(0.0, 0.0)


@dataclass
class StratSpec:
    name: str
    kind: str                       # 'mom' | 'bh' | 'ew'
    exit_ma: int | None = None      # 10 / 5
    daily_reentry: bool = False


@dataclass
class Plan:
    """Pre-computed, strategy-independent schedule: weekly (signal_pos, exec_pos, target) and monthly EW events."""
    weekly: list = field(default_factory=list)      # (signal_pos, exec_pos, {sym: w})
    monthly: list = field(default_factory=list)     # (signal_pos, exec_pos, {sym: 1/n})


class Result:
    def __init__(self):
        self.nav = None
        self.trades = []
        self.trips = []
        self.counters = {"limit_blocked_buy": 0, "limit_blocked_sell": 0, "t1_blocked_sell": 0, "nobar_blocked": 0,
                         "cash_scaled_buys": 0}
        self.rf = None
        self.postponements = []
        self.group_w = None


def fee_for(notional: float, cost: Cost, lots: bool) -> float:
    if notional <= 0:
        return 0.0
    comm = notional * cost.commission_bp / 1e4
    if lots and cost.commission_bp > 0:
        comm = max(comm, 5.0)
    return comm + notional * cost.slippage_bp / 1e4


def simulate(panel: Panel, plan: Plan, spec: StratSpec, cost: Cost, capital: float, lots: bool, curve,
             start_pos: int, end_pos: int, bh_symbol: str = "510300", postpone_on_missing: bool = False,
             groups: dict | None = None) -> Result:
    guard.assert_date_allowed(panel.sessions[end_pos])
    res = Result()
    N = len(panel.tickers)
    tick = panel.tickers
    col = panel.col
    limit = np.array([panel.price_limit.get(t, 0.10) for t in tick])
    sig_by_exec = {}
    if spec.kind == "mom":
        for sp, ep, tg in plan.weekly:
            sig_by_exec[sp] = (ep, tg)
    elif spec.kind == "ew":
        evs = [(sp, ep, tg) for sp, ep, tg in plan.monthly]
        for sp, ep, tg in evs:
            sig_by_exec[sp] = (ep, tg)
    sig_pos_set = set(sig_by_exec)
    shares = np.zeros(N)
    cash = float(capital)
    bought_pos = np.full(N, -10)
    LC = np.full(N, np.nan)
    want: dict[int, float] = {}
    pending: dict[int, dict] = {}
    exited: set[int] = set()
    latest_target: dict[int, float] = {}
    trips_open: dict[int, dict] = {}
    nav_rows = []
    gw_rows = []
    prev_nav = capital
    exit_ma = panel.sma[spec.exit_ma] if spec.exit_ma else None
    bh_j = col[bh_symbol] if spec.kind == "bh" else None
    sessions = panel.sessions

    def post_close_events(i: int):
        """Decisions at the close of session i (take effect at the open of the next session)."""
        nonlocal latest_target
        if spec.kind == "bh":
            return
        if i in sig_pos_set:
            ep, tg = sig_by_exec[i]
            if ep > end_pos:
                return
            tgj = {col[s]: w for s, w in tg.items()}
            if spec.kind == "ew":
                for j in set(np.where(shares > EPS)[0]) | set(tgj):
                    want[j] = tgj.get(j, 0.0)
                    pending[j] = {"sig": sessions[i], "why": "monthly_rebalance"}
                return
            latest_target = dict(tgj)
            union = set(np.where(shares > EPS)[0]) | set(tgj) | exited
            for j in union:
                tw = tgj.get(j, 0.0)
                held = shares[j] > EPS
                if tw > 0 and exit_ma is not None:
                    below = bool(panel.C[i, j] < exit_ma[i, j]) if not np.isnan(panel.C[i, j]) else False
                    if held and below:
                        want[j] = 0.0
                        exited.add(j)
                        why = f"ma{spec.exit_ma}_exit_at_weekly"
                    elif (not held) and (j in exited) and below:
                        want[j] = 0.0
                        why = "stay_exited"
                    else:
                        want[j] = tw
                        exited.discard(j)
                        why = "weekly_rebalance"
                elif tw > 0:
                    want[j] = tw
                    why = "weekly_rebalance"
                else:
                    want[j] = 0.0
                    exited.discard(j)
                    why = "weekly_deselect"
                pending[j] = {"sig": sessions[i], "why": why}
            return
        # non-weekly day: daily exit / re-entry (S2-S4)
        if exit_ma is not None and spec.kind == "mom":
            for j in np.where(shares > EPS)[0]:
                c = panel.C[i, j]
                if not np.isnan(c) and c < exit_ma[i, j] and want.get(j, 1.0) > 0:
                    want[j] = 0.0
                    exited.add(int(j))
                    pending[int(j)] = {"sig": sessions[i], "why": f"ma{spec.exit_ma}_exit"}
            if spec.daily_reentry:
                for j in list(exited):
                    c = panel.C[i, j]
                    tw = latest_target.get(j, 0.0)
                    if tw > 0 and shares[j] <= EPS and not np.isnan(c) and c > exit_ma[i, j]:
                        want[j] = tw
                        exited.discard(j)
                        pending[j] = {"sig": sessions[i], "why": "daily_reentry"}

    def px_open(j, i):
        o = panel.O[i, j]
        return o if not np.isnan(o) else LC[j]

    for i in range(start_pos, end_pos + 1):
        d = sessions[i]
        # 1. cash interest (none on the first scored session)
        if i > start_pos:
            cash *= curve.accrual_factor(sessions[i - 1], d)
        # 1b. initial entry for buy-and-hold / first scheduled orders
        if spec.kind == "bh" and i == start_pos:
            want[bh_j] = 1.0
            pending[bh_j] = {"sig": sessions[i - 1], "why": "initial"}
        if spec.kind != "bh" and i == start_pos:
            # first scheduled execution: orders were set at the previous close (signal_pos = i-1 ... see run_dev)
            ep_sig = [sp for sp, (ep, tg) in sig_by_exec.items() if ep == i]
            if ep_sig:                    # (common-window runs may start before the first scheduled execution)
                post_close_events(ep_sig[0])
        # 2. open: refresh last close for symbols with bars (LC holds last CLOSE before today)
        # 3. process pending orders
        pending_skip = False
        if pending and postpone_on_missing:
            # cn_transfer amendment 1: the whole rebalance waits until every held / target symbol has a bar
            need = set(np.where(shares > EPS)[0]) | {j for j in pending if want.get(j, 0.0) > 0}
            miss = [tick[j] for j in sorted(need) if np.isnan(panel.O[i, j]) or np.isnan(panel.C[i, j])]
            if miss:
                res.postponements.append({"date": d, "missing": ",".join(miss)})
                pending_skip = True
        if pending and not pending_skip:
            Vpre = cash
            for j in np.where(shares > EPS)[0]:
                p = px_open(j, i)
                Vpre += shares[j] * (p if not np.isnan(p) else 0.0)
            sells, buys = [], []
            for j, meta in sorted(pending.items()):
                w = want.get(j, 0.0)
                p = panel.O[i, j]
                have_bar = not np.isnan(p)
                cur = shares[j] * (p if have_bar else (LC[j] if not np.isnan(LC[j]) else 0.0))
                tgt = w * Vpre
                if w == 0.0:
                    if shares[j] > EPS:
                        sells.append((j, shares[j], meta))
                    else:
                        pending[j]["done"] = True
                    continue
                delta = tgt - cur
                if abs(delta) <= 1e-7 * max(Vpre, 1.0):
                    pending[j]["done"] = True
                elif delta < 0:
                    if not have_bar:
                        res.counters["nobar_blocked"] += 1
                        continue
                    sh = min(shares[j], -delta / p)
                    if lots:
                        sh = np.floor(sh / 100.0) * 100.0
                        if sh <= 0:
                            pending[j]["done"] = True
                            continue
                    sells.append((j, sh, meta))
                else:
                    buys.append((j, delta, meta))
            for j, sh, meta in sells:
                p = panel.O[i, j]
                if np.isnan(p):
                    res.counters["nobar_blocked"] += 1
                    continue
                if bought_pos[j] == i:
                    res.counters["t1_blocked_sell"] += 1
                    continue
                pc = panel.PC[i, j]
                if not np.isnan(pc) and p <= pc * (1 - limit[j]) + TICK_TOL:
                    res.counters["limit_blocked_sell"] += 1
                    continue
                notional = sh * p
                fee = fee_for(notional, cost, lots)
                cash += notional - fee
                shares[j] -= sh
                if shares[j] < 1e-9:
                    shares[j] = 0.0
                res.trades.append(dict(date=d, ticker=tick[j], side="SELL", shares=sh, price=p, notional=notional, fee=fee,
                                       reason=meta["why"], signal_date=meta["sig"]))
                trip = trips_open.get(j)
                if trip is not None:
                    trip["proceeds"] += notional - fee
                    if shares[j] <= EPS:
                        trip["exit"] = d
                        res.trips.append(trip)
                        del trips_open[j]
                meta["done"] = True
            # buys
            ok_buys = []
            for j, delta, meta in buys:
                p = panel.O[i, j]
                if np.isnan(p):
                    res.counters["nobar_blocked"] += 1
                    continue
                pc = panel.PC[i, j]
                if not np.isnan(pc) and p >= pc * (1 + limit[j]) - TICK_TOL:
                    res.counters["limit_blocked_buy"] += 1
                    continue
                ok_buys.append((j, delta, meta, p))
            if ok_buys:
                need = sum(dl + fee_for(dl, cost, lots) for _, dl, _, _ in ok_buys)
                f = min(1.0, max(cash, 0.0) / need) if need > 0 else 1.0
                if f < 0.999:
                    res.counters["cash_scaled_buys"] += 1
                for j, delta, meta, p in ok_buys:
                    alloc = delta * f
                    if lots:
                        n = np.floor(alloc / (100.0 * p)) * 100.0
                        while n > 0 and n * p + fee_for(n * p, cost, lots) > cash + 1e-9:
                            n -= 100.0
                        sh = n
                    else:
                        sh = alloc / p
                        if sh * p + fee_for(sh * p, cost, lots) > cash + 1e-9:
                            sh = max(cash, 0.0) / (p * (1 + cost.bp / 1e4))
                    if sh <= 0:
                        meta["done"] = True
                        continue
                    notional = sh * p
                    fee = fee_for(notional, cost, lots)
                    cash -= notional + fee
                    shares[j] += sh
                    bought_pos[j] = i
                    res.trades.append(dict(date=d, ticker=tick[j], side="BUY", shares=sh, price=p, notional=notional,
                                           fee=fee, reason=meta["why"], signal_date=meta["sig"]))
                    trip = trips_open.get(j)
                    if trip is None:
                        trips_open[j] = dict(ticker=tick[j], entry=d, spent=notional + fee, proceeds=0.0, exit=None)
                    else:
                        trip["spent"] += notional + fee
                    if f >= 0.999:
                        meta["done"] = True
            for j in [j for j, m in pending.items() if m.get("done")]:
                del pending[j]
        # 4. close valuation
        mv = 0.0
        for j in np.where(shares > EPS)[0]:
            c = panel.C[i, j]
            if np.isnan(c):
                c = LC[j]
            mv += shares[j] * c
        # update LC after valuation use
        has = ~np.isnan(panel.C[i])
        LC[has] = panel.C[i][has]
        nav = cash + mv
        if nav <= 0:
            raise RuntimeError(f"NAV wiped out at {d}")
        nav_rows.append((d, nav, nav / prev_nav - 1.0, cash, mv / nav))
        if groups is not None:
            gv = {}
            for j in np.where(shares > EPS)[0]:
                c = panel.C[i, j]
                c = LC[j] if np.isnan(c) else c
                gname = groups.get(tick[j], "other")
                gv[gname] = gv.get(gname, 0.0) + shares[j] * c
            gw_rows.append({"equity": gv.get("equity", 0.0) / nav, "gold": gv.get("gold", 0.0) / nav, "bond": gv.get("bond", 0.0) / nav})
        prev_nav = nav
        # 5. close decisions
        if i < end_pos:
            post_close_events(i)
    res.nav = pd.DataFrame(nav_rows, columns=["date", "nav", "ret", "cash", "gross_exposure"])
    if groups is not None:
        res.group_w = pd.DataFrame(gw_rows)
    res.open_trips = list(trips_open.values())
    res.final_shares = {tick[j]: float(shares[j]) for j in np.where(shares > EPS)[0]}
    return res
