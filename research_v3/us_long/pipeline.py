"""Signal / frame construction shared by the primary run, the challenger run and the secondary runs."""
from __future__ import annotations

import bisect
import math
from types import SimpleNamespace
from typing import Callable, Mapping, Sequence

import pandas as pd

from research_v2.contracts import SignalFrame
from research_v2.signals import compute_matched_monthly_signals
from research_v3.cash_yield import CashYieldCurve
from research_v3.cn_transfer.calendar_cn import build_monthly_matched_schedule_cal, build_weekly_schedule_cal
from research_v3.cn_transfer.signals_cn import cap_def_frame, compute_model_signals_cn
from research_v3.signals_step3 import make_defensive_provider
from research_v3.step4 import benchmark_frames

from .calendar_proxy import ProxyCalendar, as_of_after_close
from .proxies import EQUITY_GROUPS, GROUPS, UNIVERSE


def build_core(cal, sessions: Sequence[str], bars: Mapping[str, pd.DataFrame], curve: CashYieldCurve,
               universe: Sequence[str] = UNIVERSE, groups: Mapping[str, Sequence[str]] = GROUPS,
               def_symbol: str = "IEF", end: str | None = None, trend: int = 40, mom: int = 12):
    """Frozen CAP_DEF_M construction on a given calendar / signal bars. Returns a SimpleNamespace with
    weekly, monthly events, wf (weekly frame), mf (matched monthly = BASE_M), capdef, defensive."""
    end = end or sessions[-1]
    as_of = as_of_after_close(cal, end)
    weekly = build_weekly_schedule_cal(cal, list(sessions), as_of)
    monthly = build_monthly_matched_schedule_cal(cal, list(sessions), as_of, weekly)
    wf = compute_model_signals_cn(bars, universe, sessions, weekly, f"W_M{mom:02d}_T{trend}", mom, trend)
    mf = compute_matched_monthly_signals(wf, monthly, f"M_M{mom:02d}_T{trend}")
    defensive = make_defensive_provider(bars, sessions, curve, symbol=def_symbol, months=3)
    capdef = cap_def_frame(mf, "CAP_DEF_M", universe=universe, cap_groups=groups, defensive=defensive,
                           def_symbol=def_symbol, top_n=3)
    return SimpleNamespace(weekly=weekly, monthly=monthly, wf=wf, mf=mf, capdef=capdef, defensive=defensive)


def first_fully_warm_row(mf: SignalFrame, universe: Sequence[str], available: Mapping[str, str | None]) -> int | None:
    """First COMPLETE monthly row where every asset that has an observation on or before the row's source weekly
    signal session has a finite momentum score and a computable SMA ("all then-available assets warmed up")."""
    for i, (r, lin) in enumerate(zip(mf.rows, mf.lineage)):
        if r["status"] != "COMPLETE":
            continue
        src = r.get("source_signal_session") or r["signal_session"]
        ok, n = True, 0
        for s in universe:
            fd = available.get(s)
            if fd is None or fd > src:
                continue
            n += 1
            if not (r["momentum_scores"].get(s) is not None and math.isfinite(r["momentum_scores"][s])
                    and lin["symbols"][s]["sma_k"] is not None):
                ok = False
                break
        if ok and n:
            return i
    return None


def strategy_frames(mf, capdef, score_start, score_end, bars_spy: pd.DataFrame, def_symbol="IEF"):
    """Frozen rule, BASE and the three protocol benchmarks (step4.benchmark_frames, reused)."""
    view = SimpleNamespace(bars={"SPY": bars_spy}, score_start=score_start, score_end=score_end)
    bench = benchmark_frames(view, mf.rows)
    return {"CAP_DEF_M": capdef, "BASE_M": mf, "SPY_BH": bench["SPY_BH"], "US_60_40": bench["60_40"],
            "SPY_SMA10": bench["SPY_SMA10"]}


# ---------------------------------------------------------------- challenger
def build_challenger_rows(capdef_rows: Sequence[Mapping], weekly_rows: Sequence[Mapping],
                          monthly_signal_sessions: set, defensive: Callable[[str], str], *,
                          equity_symbols: set, def_symbol: str, score_start: str, score_end: str,
                          weekly_check: bool = True) -> tuple[list[dict], list[dict]]:
    """CRASH_WEEKLY_EXIT signal rows: the frozen monthly rows (kind "full") plus weekly "exit" rows.

    At each weekly signal session w that is not also a monthly signal session, for each equity-group asset in the
    target state in force at w's close (last executed full/exit row with execution_session <= w), if its frozen
    40-week trend test fails (weekly row trend_passes), it is sold at w's next execution and its weight goes to
    def_symbol if defensive(w) == def_symbol, else to cash. No re-entry until the next monthly row (which restarts
    the state from the frozen target). Gold / bonds are never touched. Exit rows are cumulative since the last
    monthly row (executing an already-sold symbol is a no-op). Returns (rows, exit_events log)."""
    full = [dict(r, kind="full") for r in capdef_rows if r.get("status") == "COMPLETE"
            and score_start <= r["execution_session"] <= score_end]
    full.sort(key=lambda r: r["execution_session"])
    out = list(full)
    log: list[dict] = []
    if not weekly_check:
        return out, log
    state: dict[str, float] = {}
    cum: dict[str, str] = {}
    fi = 0
    for w in sorted(weekly_rows, key=lambda r: r["signal_session"]):
        ws = w["signal_session"]
        while fi < len(full) and full[fi]["execution_session"] <= ws:
            state = {k: v for k, v in full[fi]["weights"].items() if k != "CASH" and v > 1e-12}
            state["CASH"] = full[fi]["weights"].get("CASH", 0.0)
            cum = {}
            fi += 1
        if ws in monthly_signal_sessions or w.get("status") != "COMPLETE":
            continue
        if not (score_start <= w["execution_session"] <= score_end):
            continue
        held = [s for s, v in state.items() if s in equity_symbols and v > 1e-12]
        fails = [s for s in held if not w["trend_passes"].get(s, False)]
        if not fails:
            continue
        dest = def_symbol if defensive(ws) == def_symbol else "CASH"
        for s in fails:
            state[dest] = state.get(dest, 0.0) + state.pop(s)
            cum[s] = dest
        out.append({"kind": "exit", "signal_session": ws, "execution_session": w["execution_session"],
                    "status": "COMPLETE", "exits": dict(cum), "weights": dict(state)})
        log.append({"signal_session": ws, "execution_session": w["execution_session"], "exited": ",".join(fails),
                    "dest": dest})
    out.sort(key=lambda r: (r["execution_session"], 0 if r["kind"] == "full" else 1))
    return out, log
