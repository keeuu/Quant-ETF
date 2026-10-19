"""Deterministic forward replay of the frozen CAP_DEF_M per market, plus the dashboard indicators.

All strategy logic comes from the frozen research code by import: schedule builders (research_v3.cn_transfer.
calendar_cn), signals (research_v2.signals / cn_transfer.signals_cn / research_v3.signals_step3), the engine
(research_v3.cn_transfer.engine_cn.run_backtest_cn, tested equal to research_v3.engine on US data) and the cash
curves. This module only wires them together, trims the data to finished sessions and reads results out.
"""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

import numpy as np
import pandas as pd

from research_v2.contracts import PeriodEvent
from research_v2.signals import compute_matched_monthly_signals
from research_v3.cash_yield import CashYieldCurve
from research_v3.cn_transfer.calendar_cn import (build_monthly_matched_schedule_cal, build_weekly_schedule_cal,
                                                 market_as_of_after_close_cal)
from research_v3.cn_transfer.engine_cn import run_backtest_cn
from research_v3.cn_transfer.runner import strategy_frames
from research_v3.cn_transfer.signals_cn import ShiborCurve, cap_def_frame, compute_model_signals_cn
from research_v3.config import V3Config
from research_v3.signals_step3 import make_defensive_provider, reselect3
from research_v3.step4 import benchmark_frames

from .calendar_hybrid import HybridCalendar
from .config import INITIAL_NAV, SESSION_MARGIN_MIN, SHANGHAI, MarketSpec


class ShadowError(RuntimeError):
    pass


def _d(ts) -> str:
    return pd.Timestamp(ts).strftime("%Y-%m-%d")


# ------------------------------------------------------------------------------------------------ sessions
def known_sessions(bars: dict) -> list[str]:
    """Dates on which at least half of the universe has a bar (weekdays only)."""
    counts: dict[str, int] = {}
    for df in bars.values():
        for d in df["Date"]:
            counts[d] = counts.get(d, 0) + 1
    n = len(bars)
    return sorted(d for d, c in counts.items() if c * 2 >= n and pd.Timestamp(d).dayofweek < 5)


def finished_sessions(spec: MarketSpec, base_cal, known: list[str], now_utc: datetime) -> list[str]:
    """Drop trailing sessions that have not closed yet (a bar fetched during the day is partial)."""
    cal = HybridCalendar(base_cal, known, spec.tz, spec.open_hm, spec.close_hm)
    now = pd.Timestamp(now_utc).tz_convert("UTC") if pd.Timestamp(now_utc).tzinfo else pd.Timestamp(now_utc, tz="UTC")
    out = list(known)
    while out and cal.session_close(out[-1]) + pd.Timedelta(minutes=SESSION_MARGIN_MIN) > now:
        out.pop()
    return out


def true_month_ends(cal, monthly: list[PeriodEvent]) -> list[PeriodEvent]:
    """Keep only events whose signal session is the last session of its month.

    The research builder treats the last available session of an unfinished month as a month end (harmless for
    the historical samples, which end on a month end); a live dashboard must not."""
    return [e for e in monthly if _d(cal.next_session(e.signal_session))[:7] != e.signal_session[:7]]


# ------------------------------------------------------------------------------------------------ frozen pipeline
def make_curve(spec: MarketSpec, rate: pd.Series) -> CashYieldCurve:
    return ShiborCurve(rate) if spec.key == "CN" else CashYieldCurve(rate)


def apply_cap_def(spec: MarketSpec, mf, bars, sessions, curve):
    defensive = make_defensive_provider(bars, sessions, curve, symbol=spec.def_symbol, months=spec.defensive_months)
    if spec.key == "US":
        frame = reselect3(mf, "M_CAP_DEF", cap_groups=spec.groups, slots="def", sigma=None, defensive=defensive,
                          top_n=spec.top_n)
    else:
        frame = cap_def_frame(mf, "CAP_DEF_M", universe=spec.universe, cap_groups=spec.groups,
                              defensive=defensive, def_symbol=spec.def_symbol, top_n=spec.top_n)
    return frame, defensive


def frozen_frames(spec: MarketSpec, bars, sessions, cal, curve, as_of):
    """weekly events, true monthly events, weekly frame, matched-monthly frame, CAP_DEF frame, defensive provider."""
    weekly = build_weekly_schedule_cal(cal, sessions, as_of)
    if not weekly:
        raise ShadowError("没有完整的周事件(数据不足)")
    monthly = true_month_ends(cal, build_monthly_matched_schedule_cal(cal, sessions, as_of, weekly))
    wf = compute_model_signals_cn(bars, spec.universe, sessions, weekly, f"W_M{spec.momentum_months}_T{spec.trend_weeks}",
                                  spec.momentum_months, spec.trend_weeks)
    mf = compute_matched_monthly_signals(wf, monthly, f"M_M{spec.momentum_months}_T{spec.trend_weeks}")
    capdef, defensive = apply_cap_def(spec, mf, bars, sessions, curve)
    return weekly, monthly, wf, mf, capdef, defensive


def run_replay(spec: MarketSpec, cal, bars, curve, mf, capdef, score_start: str, score_end: str, initial_nav: float):
    """Run CAP_DEF_M and both benchmarks over [score_start, score_end] with the frozen cost/cash settings."""
    cfg = V3Config(cost_bps=5.0, slippage_bps=5.0, cash_yield_source="irx", initial_nav=initial_nav)
    if spec.key == "US":
        bf = benchmark_frames(SimpleNamespace(bars=bars, score_start=score_start, score_end=score_end), mf.rows)
        frames = {"strat": capdef, "hold": bf["SPY_BH"], "mix": bf["60_40"]}
    else:
        sf = strategy_frames(mf, capdef, score_start, score_end, spec.universe)
        frames = {"strat": capdef, "hold": sf["CSI300_BH"], "mix": sf["CN_60_40"]}
    out = {}
    for k, fr in frames.items():
        nav, reb, met = run_backtest_cn(cal, bars, score_start, score_end, fr, cfg, curve, spec.universe,
                                        suspension_rules=spec.suspension_rules)
        out[k] = {"nav": nav, "reb": reb, "metrics": met}
    return out


def first_post_freeze_row(capdef, freeze_date: str):
    for r in capdef.rows:
        if r["execution_session"] >= freeze_date:
            return r
    return None


# ------------------------------------------------------------------------------------------------ read-outs
def _px(bars, sym, date, col):
    df = bars[sym]
    hit = df.loc[df["Date"] == date, col]
    return None if hit.empty else float(hit.iloc[0])


def holdings_from_run(spec: MarketSpec, run: dict, capdef, bars, last_session: str):
    """Current holdings from the replay: target weights of the last rebalance, entry dates, return since entry.
    shares_i = w_i * V_post / adj_open(exec) is exact for the self-financing ledger (V_post + fee = V_pre)."""
    reb, nav = run["reb"], run["nav"]
    by_sig = {r["signal_session"]: r for r in capdef.rows}
    entry: dict[str, str] = {}
    prev: set = set()
    last = None
    for rb in reb.itertuples():
        row = by_sig[rb.signal_session]
        w = {s: float(row["weights"].get(s, 0.0)) for s in spec.universe}
        held = {s for s, v in w.items() if v > 1e-9}
        for s in held - prev:
            entry[s] = rb.execution_session
        for s in prev - held:
            entry.pop(s, None)
        prev = held
        last = (rb, w, row)
    if last is None:
        return None
    rb, w, row = last
    nav_last = float(nav["nav"].iloc[-1])
    shares = {s: w[s] * float(rb.V_post) / _px(bars, s, rb.execution_session, "adj_open") for s in spec.universe if w[s] > 1e-9}
    rows, tot = [], 0.0
    for s, q in shares.items():
        p = _px(bars, s, last_session, "adj_close")
        if p is None:                       # suspended on the last session: carry the last available close
            p = float(bars[s][bars[s]["Date"] <= last_session]["adj_close"].iloc[-1])
        mv = q * p
        tot += mv
        e0 = _px(bars, s, entry[s], "adj_open")
        rows.append({"symbol": s, "name": spec.names.get(s, s), "target_weight": w[s], "weight": mv / nav_last,
                     "entry_date": entry[s], "ret_since_entry": p / e0 - 1.0 if e0 else None})
    rows.sort(key=lambda r: -r["weight"])
    cash_w = max(0.0, 1.0 - tot / nav_last)
    return {"rows": rows, "cash_weight": cash_w, "target_cash_weight": float(row["weights"].get("CASH", 0.0)),
            "rebalance_date": rb.execution_session, "signal_session": rb.signal_session,
            "last_fee": float(rb.fee), "last_turnover": float(rb.one_way_turnover)}


def series_stats(nav: pd.DataFrame, initial: float) -> dict:
    v = nav["nav"].astype(float).to_numpy()
    peak_run = np.maximum.accumulate(np.concatenate([[initial], v]))[1:]
    dd = v / peak_run - 1.0
    return {"nav": float(v[-1]), "day_return": float(nav["daily_return"].iloc[-1]),
            "cum_return": float(v[-1] / initial - 1.0), "drawdown": float(dd[-1]), "max_drawdown": float(dd.min()),
            "peak": float(peak_run[-1])}


def _event(cal, session: str, pid: str, source: str | None = None) -> PeriodEvent:
    nxt = cal.next_session(session)
    close = cal.session_close(pd.Timestamp(session)).isoformat()
    return PeriodEvent(period_id=pid, signal_session=session, signal_close_ts=close,
                       execution_session=_d(nxt), execution_open_ts=cal.session_open(nxt).isoformat(),
                       feature_cutoff=close, status="PENDING", source_signal_session=source)


def what_if(spec: MarketSpec, bars, sessions, cal, curve, weekly, wf, defensive) -> dict:
    """Signal the frozen rule would produce if the latest finished close were the (weekly and monthly) signal close.
    Uses the frozen functions on a hypothetical trailing event; nothing is traded or stored."""
    last = sessions[-1]
    if weekly[-1].signal_session == last:
        wf2 = wf
    else:
        wf2 = compute_model_signals_cn(bars, spec.universe, sessions, weekly + [_event(cal, last, "WHATIF_W")],
                                       "WHATIF_W", spec.momentum_months, spec.trend_weeks)
    if not wf2.rows or wf2.rows[-1]["signal_session"] != last:
        raise ShadowError("what-if 信号无法计算(历史不足)")
    mf2 = compute_matched_monthly_signals(wf2, [_event(cal, last, "WHATIF_M", source=last)], "WHATIF_M")
    cap2, _ = apply_cap_def(spec, mf2, bars, sessions, curve)
    row, lin = cap2.rows[0], wf2.lineage[-1]
    official = wf.rows[-1] if wf.rows else None
    trend = []
    for s in spec.universe:
        L = lin["symbols"][s]
        close, sma = L["current_weekly_close"], L["sma_k"]
        trend.append({"symbol": s, "name": spec.names.get(s, s), "close": float(close), "sma": sma,
                      "dist": (float(close) / sma - 1.0) if sma else None,
                      "passes": bool(row["trend_passes"][s]),
                      "passes_official": None if official is None else bool(official["trend_passes"].get(s, False))})
    info = defensive.info(last)
    return {"signal_session": last, "execution_session_if": _d(cal.next_session(last)),
            "weights": {k: float(v) for k, v in row["weights"].items()}, "selected": list(row["selected_symbols"]),
            "eligible": list(row.get("eligible_symbols", [])), "def_weight": float(row.get("def_ief_weight",
                                                                                          row.get("def_bond_weight", 0.0))),
            "momentum": {k: (None if v is None else float(v)) for k, v in row["momentum_scores"].items()},
            "defensive_info": None if info is None else {"asset_ret": float(info[0]), "bill_ret": float(info[1])},
            "trend": trend}


def diff_weights(universe, ref: dict, new: dict, tol: float = 1e-9) -> list[dict]:
    out = []
    for s in list(universe) + ["CASH"]:
        a, b = float(ref.get(s, 0.0)), float(new.get(s, 0.0))
        if a <= tol and b <= tol:
            continue
        kind = "新增" if a <= tol else "清仓" if b <= tol else "加仓" if b > a + tol else "减仓" if b < a - tol else "不变"
        out.append({"symbol": s, "from": a, "to": b, "kind": kind})
    return out


def next_rebalance(cal, sessions: list[str], monthly, today_local: pd.Timestamp) -> dict:
    last = sessions[-1]
    known = set(sessions)
    if monthly and monthly[-1].status == "PENDING":
        sig, exe = monthly[-1].signal_session, monthly[-1].execution_session
    else:
        lt = pd.Timestamp(last)
        fut = cal.sessions_in_range(lt + pd.Timedelta(days=1), lt + pd.offsets.MonthEnd(0))
        sig = _d(fut[-1]) if len(fut) else last
        exe = _d(cal.next_session(sig))

    def exact(d):
        return d in known or cal.covered(d)
    est = not (exact(sig) and exact(exe))
    n_sessions = len(cal.sessions_in_range(pd.Timestamp(last) + pd.Timedelta(days=1), exe))
    return {"signal_session": sig, "execution_session": exe, "estimated": est, "sessions_left": int(n_sessions),
            "days_left": int((pd.Timestamp(exe) - today_local.normalize().tz_localize(None)).days)}


def data_quality_notes(spec: MarketSpec, bars, sessions, today_local: pd.Timestamp) -> list[str]:
    notes = []
    last = pd.Timestamp(sessions[-1])
    lag = (today_local.tz_localize(None).normalize() - last).days
    if lag > 4:
        notes.append(f"最新交易日 {sessions[-1]} 距今 {lag} 天，数据可能滞后")
    for s, df in bars.items():
        r = df["adj_close"].astype(float).pct_change().iloc[-250:]
        bad = df["Date"].iloc[-250:][r.abs() > 0.15]
        if len(bad):
            notes.append(f"{s} 近一年存在单日涨跌幅超过15%：{', '.join(bad.tolist()[:3])}")
        if df["Date"].iloc[-1] < sessions[-1]:
            notes.append(f"{s} 缺少最新交易日 {sessions[-1]} 的行情(按停牌处理)")
    return notes


# ------------------------------------------------------------------------------------------------ main entry
def compute_market_view(spec: MarketSpec, bars: dict, rate: pd.Series, now_utc: datetime, *, base_cal,
                        freeze_date: str, initial_nav: float = INITIAL_NAV, meta: dict | None = None,
                        account: dict | None = None) -> dict:
    meta = meta or {}
    now_utc = now_utc if now_utc.tzinfo else now_utc.replace(tzinfo=timezone.utc)
    today_local = pd.Timestamp(now_utc.astimezone(SHANGHAI))   # countdowns are in the user's (Shanghai) days
    known = finished_sessions(spec, base_cal, known_sessions(bars), now_utc)
    if len(known) < 300:
        raise ShadowError(f"可用交易日不足({len(known)})")
    sessions = known
    last = sessions[-1]
    bars = {s: df[df["Date"] <= last].reset_index(drop=True) for s, df in bars.items()}
    cal = HybridCalendar(base_cal, sessions, spec.tz, spec.open_hm, spec.close_hm)
    curve = make_curve(spec, rate)
    as_of = market_as_of_after_close_cal(cal, last, spec.tz)
    weekly, monthly, wf, mf, capdef, defensive = frozen_frames(spec, bars, sessions, cal, curve, as_of)
    if not capdef.rows:
        raise ShadowError("没有可用的月度信号(历史不足)")

    view: dict[str, Any] = {
        "key": spec.key, "title": spec.title, "status": "ok", "as_of": last, "freeze_date": freeze_date,
        "initial_nav": initial_nav, "bench_hold_label": spec.bench_hold_label, "bench_mix_label": spec.bench_mix_label,
        "cash_label": spec.cash_label, "def_symbol": spec.def_symbol, "sources": meta.get("sources", {}), "rate_source": meta.get("rate_source", ""),
        "fallback_used": bool(meta.get("fallback_used", False)), "fetch_notes": list(meta.get("notes", [])),
        "fetched_at_utc": meta.get("fetched_at_utc", ""), "universe": list(spec.universe),
        "names": {s: spec.names.get(s, s) for s in list(spec.universe)},
    }

    # --- latest official month-end signal (reference portfolio at/near the freeze)
    ref = capdef.rows[-1]
    view["reference_target"] = {"signal_session": ref["signal_session"], "execution_session": ref["execution_session"],
                                "status": ref["status"], "pre_freeze": ref["execution_session"] < freeze_date,
                                "weights": {k: float(v) for k, v in ref["weights"].items()},
                                "selected": list(ref["selected_symbols"])}

    # --- post-freeze window
    first = first_post_freeze_row(capdef, freeze_date)
    complete_first = first is not None and first["status"] == "COMPLETE" and first["execution_session"] <= last
    view["first_execution"] = (first["execution_session"] if first else None)
    view["first_execution_signal"] = (first["signal_session"] if first else None)
    view["window"] = {"start": None, "end": None, "n_sessions": 0, "empty": True}
    view["series"] = None
    view["holdings"] = None
    runs = None
    if complete_first:
        score_start = first["execution_session"]
        runs = run_replay(spec, cal, bars, curve, mf, capdef, score_start, last, initial_nav)
        nav = runs["strat"]["nav"]
        n = len(nav)
        view["window"] = {"start": score_start, "end": last, "n_sessions": n, "empty": False}
        view["strategy"] = series_stats(nav, initial_nav)
        view["bench_hold"] = series_stats(runs["hold"]["nav"], initial_nav)
        view["bench_mix"] = series_stats(runs["mix"]["nav"], initial_nav)
        view["series"] = {"dates": nav["date"].tolist(), "strat": nav["nav"].astype(float).tolist(),
                          "hold": runs["hold"]["nav"]["nav"].astype(float).tolist(),
                          "mix": runs["mix"]["nav"]["nav"].astype(float).tolist()}
        view["holdings"] = holdings_from_run(spec, runs["strat"], capdef, bars, last)
        view["total_fees"] = float(nav["fee"].sum())
        if spec.suspension_rules and runs["strat"]["metrics"].get("postponements"):
            view["postponements"] = runs["strat"]["metrics"]["postponements"]

    # --- what-if + trend distance (always available)
    wi = what_if(spec, bars, sessions, cal, curve, weekly, wf, defensive)
    if view["holdings"]:
        basis = {r["symbol"]: r["target_weight"] for r in view["holdings"]["rows"]}
        basis["CASH"] = view["holdings"]["target_cash_weight"]
        basis_label = f"当前目标组合(自 {view['holdings']['rebalance_date']} 起)"
    else:
        basis = view["reference_target"]["weights"]
        basis_label = f"冻结时参考组合(月末信号 {view['reference_target']['signal_session']})"
    wi["basis_label"] = basis_label
    wi["changes"] = diff_weights(spec.universe, basis, wi["weights"])
    wi["changed"] = any(c["kind"] != "不变" for c in wi["changes"])
    view["what_if"] = wi
    view["trend"] = wi["trend"]

    view["next_rebalance"] = next_rebalance(cal, sessions, monthly, today_local)
    view["quality_notes"] = data_quality_notes(spec, bars, sessions, today_local)
    view["orders"] = _orders_for(view, bars, account, meta.get("extra_prices"))
    return view


def _orders_for(view: dict, bars: dict, account: dict | None, extra_prices: dict | None):
    """Order list for a real account (dashboard.orders). Read-only on the finished view; a failure here must never
    take the market down, and account={"enabled": False} switches it off (used by the identity test)."""
    from . import config
    from .orders import compute_orders
    acct = config.load_account().get(view["key"]) if account is None else account
    if not acct or acct.get("enabled") is False:
        return None
    try:
        return compute_orders(view, bars, acct, extra_prices)
    except Exception as e:  # noqa: BLE001
        return {"error": f"{type(e).__name__}: {e}"}
