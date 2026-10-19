"""cb_rotation engine: eligibility, double-low score, hold-10 / keep-while-rank<=20 selection, order-level simulation.

Timing: everything dated s uses data up to the close of session s. Signals (last session of each ISO week) and forced-exit
checks (every session close) create orders that execute at the NEXT session's open. Orders blocked by a price limit (from
2022-08-01) or by a missing bar retry at each following open; a new signal replaces pending buys, pending sells persist.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .data import NEVER, CBPanel

LIMIT_START = "2022-08-01"
LIMIT_PCT = 0.20
TOP_N = 10
KEEP_RANK = 20
MIN_LISTED = 10
MIN_AMOUNT = 5_000_000.0
AMOUNT_WIN = 20
MAT_WIN = 20
MAX_PRICE = 150.0
LOT = 10
PREMIUM_WEIGHT = 100.0


@dataclass(frozen=True)
class Costs:
    commission_bp: float = 1.0
    min_commission: float = 1.0
    slippage_bp: float = 10.0


# ------------------------------------------------------------------------------------------------ rules
def limit_active(date: str) -> bool:
    return str(date)[:10] >= LIMIT_START


def _r3(x: float) -> float:
    return np.floor(x * 1000.0 + 0.5 + 1e-9) / 1000.0


def buy_blocked(open_px: float, ref: float, date: str, has_bar: bool) -> bool:
    if not has_bar:
        return True
    if not limit_active(date) or not np.isfinite(ref):
        return False
    return open_px >= _r3(ref * (1.0 + LIMIT_PCT)) - 1e-9


def sell_blocked(open_px: float, ref: float, date: str, has_bar: bool) -> bool:
    if not has_bar:
        return True
    if not limit_active(date) or not np.isfinite(ref):
        return False
    return open_px <= _r3(ref * (1.0 - LIMIT_PCT)) + 1e-9


def commission(notional: float, costs: Costs) -> float:
    if notional <= 0:
        return 0.0
    return max(costs.min_commission, notional * costs.commission_bp / 1e4)


def buy_quantity(budget: float, price_exec: float, costs: Costs, lot: int | None) -> float:
    """Largest quantity whose cost (notional + commission incl. minimum) fits in `budget`; lot=None -> fractional."""
    if budget <= 0 or price_exec <= 0:
        return 0.0
    c = costs.commission_bp / 1e4
    n1 = budget / (price_exec * (1.0 + c))
    n2 = (budget - costs.min_commission) / price_exec if costs.min_commission > 0 else n1
    n = min(n1, n2)
    if n <= 0:
        return 0.0
    if lot:
        return float(int(n / lot + 1e-9) * lot)
    return n


def double_low(close: np.ndarray, premium: np.ndarray) -> np.ndarray:
    """Score = close + 100 x premium (decimal). Uses only values dated on the signal session."""
    return np.asarray(close, float) + PREMIUM_WEIGHT * np.asarray(premium, float)


def horizon_dates(sessions: list, win: int = MAT_WIN) -> np.ndarray:
    """Date of the session `win` sessions after each session (business-day extrapolation beyond the loaded calendar)."""
    S = len(sessions)
    out = list(sessions[win:])
    if S:
        ext = pd.bdate_range(pd.Timestamp(sessions[-1]) + pd.Timedelta(days=1), periods=win)
        pad = [d.strftime("%Y-%m-%d") for d in ext]
        out += pad[:min(win, S)]
    return np.array(out[:S])


def eligibility(p: CBPanel, exit_liquidity: bool = True) -> tuple[np.ndarray, np.ndarray]:
    """(E_full, E_exit), both S x B bool. E_full = every protocol filter (used to select / rebalance); E_exit drops the
    'has a bar with volume > 0' condition (a holding that simply did not trade today is not force-sold). With
    exit_liquidity=False (sensitivity only) E_exit also ignores the 20-session amount filter."""
    S, B = p.close.shape
    dates = np.array(p.sessions)
    n_bars = np.cumsum(np.isfinite(p.close), axis=0) + p.bars_prior[None, :]
    listed = n_bars >= MIN_LISTED
    # 20-session mean amount; sessions without a bar count as zero; window shorter than 20 near listing
    a = np.nan_to_num(p.amount)
    cs = np.vstack([np.zeros((1, B)), np.cumsum(a, axis=0)])
    rows = np.arange(S)
    lo = np.maximum(rows + 1 - AMOUNT_WIN, 0)
    tot = cs[rows + 1] - cs[lo]
    sess_since = np.where(rows[:, None] >= p.first_bar_idx[None, :], rows[:, None] - p.first_bar_idx[None, :] + 1, 0) \
        + p.bars_prior[None, :]
    den = np.minimum(AMOUNT_WIN, np.maximum(sess_since, 1))
    amt_ok = (tot / den) >= MIN_AMOUNT
    notice_ok = dates[:, None] < p.notice[None, :]
    hz = horizon_dates(p.sessions)
    mat_ok = hz[:, None] < p.maturity[None, :]
    st_ok = ~p.st
    price_ok = p.close_ff <= MAX_PRICE
    started = rows[:, None] >= p.first_bar_idx[None, :]
    base = listed & amt_ok & notice_ok & mat_ok & st_ok & price_ok & started
    E_exit = base if exit_liquidity else (listed & notice_ok & mat_ok & st_ok & price_ok & started)
    E_full = base & p.has_bar
    return E_full, E_exit


def select_targets(score: np.ndarray, elig: np.ndarray, held: np.ndarray, codes, n: int = TOP_N,
                   keep_rank: int = KEEP_RANK):
    """Returns (keep, new, rank) with indices; rank is 1-based among eligible bonds with a finite score.
    keep = held bonds still eligible with rank <= keep_rank (at most n, best first); new = lowest-ranked non-held bonds
    filling the remaining slots."""
    ok = elig & np.isfinite(score)
    idx = np.where(ok)[0]
    c = np.asarray(codes)
    order = idx[np.lexsort((c[idx], score[idx]))]
    rank = {int(b): r + 1 for r, b in enumerate(order)}
    keep = [int(b) for b in order if held[b] and rank[int(b)] <= keep_rank][:n]
    new = []
    for b in order:
        if len(keep) + len(new) >= n:
            break
        if not held[b]:
            new.append(int(b))
    return keep, new, rank


def weekly_signal_rows(sessions: list) -> list:
    iso = pd.to_datetime(pd.Series(sessions)).dt.isocalendar()
    key = (iso["year"] * 100 + iso["week"]).to_numpy()
    last = list(np.where(key[1:] != key[:-1])[0]) + [len(sessions) - 1]
    return [int(i) for i in last]


# ------------------------------------------------------------------------------------------------ simulation
@dataclass
class SimResult:
    nav: pd.DataFrame
    trades: list
    holdings: list
    counters: dict
    terminal: list = field(default_factory=list)
    events: list = field(default_factory=list)


def simulate(p: CBPanel, accrual: np.ndarray, start: int, end: int, signal_rows, E_full: np.ndarray, E_exit: np.ndarray,
             capital: float, costs: Costs, lots: bool, mode: str = "topn", passive_frac: float = 0.0,
             record_trades: bool = True) -> SimResult:
    """Rows start..end are simulated; the state is initialised at the close of row start-1 (nav = capital) where a
    signal may already be evaluated. mode 'topn' = double-low strategy; 'ew' = equal weight over all eligible bonds."""
    S, B = p.close.shape
    sh = np.zeros(B)
    cash = float(capital)
    sigset = set(int(x) for x in signal_rows)
    lot = LOT if lots else None
    sells: dict = {}            # b -> reason  (sell everything)
    buys: list = []             # topn: b to open at budget pv/TOP_N
    resize: dict = {}           # ew: b -> target weight
    score_all = double_low(p.close, p.premium)
    cnt = dict(fills_buy=0, fills_sell=0, blocked_buy=0, blocked_sell=0, lot_zero=0, fees=0.0, slippage_cost=0.0,
               buy_notional=0.0, sell_notional=0.0, forced_exits=0, signal_ineligible_exits=0,
               passive_delist_held=0, redemption_delist_held=0, maturity_delist_held=0, passive_loss_value=0.0,
               unknown_type_held=0)
    trades, holdings, terminal, events = [], [], [], []
    rows = []
    sl = slice
    sb = costs.slippage_bp / 1e4

    def record(i, nav, ncash, nh, bn, sn, fe, sc, fl, bl):
        rows.append((p.sessions[i], nav, ncash, nh, bn, sn, fe, sc, fl, bl))

    def mark(i):
        px = p.close_ff[i]
        held = sh > 0
        return cash + float(np.sum(sh[held] * np.nan_to_num(px[held])))

    first = start - 1
    for i in range(first, end + 1):
        date = p.sessions[i]
        if i > first:
            cash *= accrual[i]
        fe0, sc0, bn0, sn0 = cnt["fees"], cnt["slippage_cost"], cnt["buy_notional"], cnt["sell_notional"]
        fl0, bl0 = cnt["fills_buy"] + cnt["fills_sell"], cnt["blocked_buy"] + cnt["blocked_sell"]
        # ---- open: sells first
        prev_close = p.close_ff[i - 1]
        for b in sorted(sells):
            if sh[b] <= 0:
                sells.pop(b)
                continue
            hb = bool(p.has_bar[i, b])
            if sell_blocked(p.open[i, b], prev_close[b], date, hb):
                cnt["blocked_sell"] += 1
                continue
            px = p.open[i, b] * (1.0 - sb)
            notional = sh[b] * px
            fee = commission(notional, costs)
            cash += notional - fee
            cnt["fees"] += fee
            cnt["slippage_cost"] += sh[b] * p.open[i, b] * sb
            cnt["sell_notional"] += notional
            cnt["fills_sell"] += 1
            if record_trades:
                trades.append(dict(date=date, code=p.codes[b], side="SELL", qty=sh[b], open=p.open[i, b], price=px,
                                   notional=notional, commission=fee, reason=sells[b]))
            sh[b] = 0.0
            sells.pop(b)
        # ---- open: ew partial reductions
        if mode == "ew" and resize:
            opx_all = np.where(p.has_bar[i], p.open[i], prev_close)
            pv_open = cash + float(np.sum(sh * np.nan_to_num(opx_all)))
            for b in sorted(resize):
                if sh[b] <= 0 or not p.has_bar[i, b]:
                    continue
                delta = resize[b] * pv_open - sh[b] * p.open[i, b]
                if delta < -1.0 and not sell_blocked(p.open[i, b], prev_close[b], date, True):
                    q = min(sh[b], -delta / (p.open[i, b] * (1 - sb)))
                    px = p.open[i, b] * (1.0 - sb)
                    notional = q * px
                    fee = commission(notional, costs)
                    cash += notional - fee
                    cnt["fees"] += fee
                    cnt["slippage_cost"] += q * p.open[i, b] * sb
                    cnt["sell_notional"] += notional
                    cnt["fills_sell"] += 1
                    if record_trades:
                        trades.append(dict(date=date, code=p.codes[b], side="SELL", qty=q, open=p.open[i, b], price=px,
                                           notional=notional, commission=fee, reason="resize"))
                    sh[b] -= q
        # ---- open: buys
        if buys or (mode == "ew" and resize):
            opx_all = np.where(p.has_bar[i], p.open[i], prev_close)
            pv_open = cash + float(np.sum(sh * np.nan_to_num(opx_all)))
            if mode == "topn":
                still = []
                for b in buys:
                    if sh[b] > 0:
                        continue
                    if not E_exit[i - 1, b]:
                        continue                                  # became ineligible before the order could fill
                    if buy_blocked(p.open[i, b], prev_close[b], date, bool(p.has_bar[i, b])):
                        cnt["blocked_buy"] += 1
                        still.append(b)
                        continue
                    px = p.open[i, b] * (1.0 + sb)
                    budget = min(pv_open / TOP_N, cash)
                    q = buy_quantity(budget, px, costs, lot)
                    if q <= 0:
                        cnt["lot_zero"] += 1
                        continue
                    notional = q * px
                    fee = commission(notional, costs)
                    cash -= notional + fee
                    sh[b] += q
                    cnt["fees"] += fee
                    cnt["slippage_cost"] += q * p.open[i, b] * sb
                    cnt["buy_notional"] += notional
                    cnt["fills_buy"] += 1
                    if record_trades:
                        trades.append(dict(date=date, code=p.codes[b], side="BUY", qty=q, open=p.open[i, b], price=px,
                                           notional=notional, commission=fee, reason="rebalance"))
                buys = still
            else:
                for b in sorted(resize):
                    if not p.has_bar[i, b] or not E_exit[i - 1, b]:
                        continue
                    delta = resize[b] * pv_open - sh[b] * p.open[i, b]
                    if delta <= 1.0:
                        continue
                    if buy_blocked(p.open[i, b], prev_close[b], date, True):
                        cnt["blocked_buy"] += 1
                        continue
                    px = p.open[i, b] * (1.0 + sb)
                    q = buy_quantity(min(delta, cash), px, costs, None)
                    if q <= 0:
                        continue
                    notional = q * px
                    fee = commission(notional, costs)
                    cash -= notional + fee
                    sh[b] += q
                    cnt["fees"] += fee
                    cnt["slippage_cost"] += q * p.open[i, b] * sb
                    cnt["buy_notional"] += notional
                    cnt["fills_buy"] += 1
                    if record_trades:
                        trades.append(dict(date=date, code=p.codes[b], side="BUY", qty=q, open=p.open[i, b], price=px,
                                           notional=notional, commission=fee, reason="rebalance"))
                resize = {}
        # ---- close: terminal events for bonds whose last bar is today
        ended = np.where((sh > 0) & p.terminated & (p.last_bar_idx == i))[0]
        for b in ended:
            typ = p.delist_type[b] or "unknown"
            frac = 1.0 if typ in ("redemption", "maturity") else passive_frac
            proceeds = sh[b] * p.close[i, b] * frac
            cash += proceeds
            if typ == "passive" or typ == "unknown":
                cnt["passive_delist_held" if typ == "passive" else "unknown_type_held"] += 1
                cnt["passive_loss_value"] += sh[b] * p.close[i, b] * (1.0 - frac)
            else:
                cnt[f"{typ}_delist_held"] += 1
            terminal.append(dict(date=date, code=p.codes[b], name=p.names[b], type=typ, qty=sh[b],
                                 last_close=p.close[i, b], fraction=frac, proceeds=proceeds))
            sh[b] = 0.0
            sells.pop(int(b), None)
        # ---- close: mark, forced exits, signal
        nav = mark(i)
        held = sh > 0
        for b in np.where(held)[0]:
            if not E_exit[i, b] and int(b) not in sells and i < end:
                sells[int(b)] = "forced_exit"
                kind = "signal_ineligible_exits" if i in sigset else "forced_exits"
                cnt[kind] += 1
                events.append(dict(date=date, code=p.codes[b], kind=kind))
        if i in sigset and i < end:
            if mode == "topn":
                sc = np.where(E_full[i], score_all[i], np.nan)
                keep, new, rank = select_targets(sc, E_full[i], held, p.codes)
                keepset = set(keep)
                for b in np.where(held)[0]:
                    if int(b) not in keepset:
                        sells.setdefault(int(b), "rebalance")
                buys = list(new)
                if record_trades:
                    holdings.append(dict(date=date, hold="|".join(p.codes[b] for b in keep + new),
                                         n_keep=len(keep), n_new=len(new), n_eligible=int(np.isfinite(sc).sum()),
                                         scores="|".join(f"{sc[b]:.2f}" for b in keep + new)))
            else:
                ok = E_full[i] & np.isfinite(p.close[i])
                tgt = np.where(ok)[0]
                for b in np.where(held)[0]:
                    if b not in set(tgt.tolist()):
                        sells.setdefault(int(b), "rebalance")
                resize = {int(b): 1.0 / len(tgt) for b in tgt} if len(tgt) else {}
                if record_trades:
                    holdings.append(dict(date=date, hold="", n_keep=0, n_new=0, n_eligible=len(tgt), scores=""))
        record(i, nav, cash, int(held.sum()), cnt["buy_notional"] - bn0, cnt["sell_notional"] - sn0,
               cnt["fees"] - fe0, cnt["slippage_cost"] - sc0, cnt["fills_buy"] + cnt["fills_sell"] - fl0,
               cnt["blocked_buy"] + cnt["blocked_sell"] - bl0)
    nav = pd.DataFrame(rows, columns=["date", "nav", "cash", "n_hold", "buy_notional", "sell_notional", "fees",
                                      "slippage", "fills", "blocked"])
    nav["ret"] = nav["nav"].pct_change()
    return SimResult(nav, trades, holdings, cnt, terminal, events)
