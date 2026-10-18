"""Step-3 selection / slot-filling variants (CASHSLOT, DEF, CAP, CAND) on top of v2 selection primitives.

Everything starts from a row's momentum_scores / trend_passes (v2 eligibility, ranking and tie-break via
select_top_assets); only the post-ranking selection and weight construction differ.
"""
from __future__ import annotations

import bisect
import math
from typing import Callable, Mapping, Sequence

import pandas as pd

from research_v2.contracts import SignalFrame, UNIVERSE
from research_v2.signals import find_anchor_session, select_top_assets

from .cash_yield import CashYieldCurve


def cap_select(eligible: Sequence[str], groups: Mapping[str, Sequence[str]], top_n: int) -> list[str]:
    """Walk the ranked eligible list; skip an asset if its group already has a selection."""
    group_of = {s: g for g, syms in groups.items() for s in syms}
    taken: set[str] = set()
    out: list[str] = []
    for s in eligible:
        g = group_of.get(s, s)
        if g in taken:
            continue
        taken.add(g)
        out.append(s)
        if len(out) >= top_n:
            break
    return out


def make_defensive_provider(bars: Mapping[str, pd.DataFrame], session_dates: Sequence[str], curve: CashYieldCurve,
                            symbol: str = "IEF", months: int = 3) -> Callable[[str], str]:
    """choice(signal_session) -> symbol or "CASH". Uses only data dated <= signal_session:
    asset adj_close at anchor and signal close, and IRX accrual over sessions (anchor, signal]."""
    df = bars[symbol]
    px = dict(zip(df["Date"].astype(str), df["adj_close"].astype(float)))
    sessions = list(session_dates)

    def info(sig: str):
        try:
            anchor = find_anchor_session(sig, months, sessions)
        except ValueError:
            return None
        p1, p0 = px.get(sig), px.get(anchor)
        if p1 is None or p0 is None or not (math.isfinite(p1) and math.isfinite(p0)) or p0 <= 0 or p1 <= 0:
            return None
        i0, i1 = bisect.bisect_left(sessions, anchor), bisect.bisect_right(sessions, sig) - 1
        bill = 1.0
        for i in range(i0 + 1, i1 + 1):
            bill *= curve.accrual_factor(sessions[i - 1], sessions[i])
        return p1 / p0 - 1.0, bill - 1.0

    def choice(sig: str) -> str:
        r = info(sig)
        return symbol if r is not None and r[0] > r[1] else "CASH"

    choice.info = info  # type: ignore[attr-defined]
    return choice


def build_weights(row: Mapping, *, cap_groups: Mapping[str, Sequence[str]] | None, slots: str,
                  sigma: Callable[[str, str], float | None] | None, defensive: Callable[[str], str] | None,
                  top_n: int = 3, def_symbol: str = "IEF") -> tuple[dict, list[str], float]:
    """Returns (weights incl. CASH, selected risky symbols, weight placed in def_symbol via defensive slots)."""
    sel = select_top_assets(row["momentum_scores"], row["trend_passes"], top_n=top_n)
    eligible = sel["eligible_symbols"]
    selected = cap_select(eligible, cap_groups, top_n) if cap_groups is not None else sel["selected_symbols"]
    k = len(selected)
    weights = {s: 0.0 for s in UNIVERSE}
    weights["CASH"] = 0.0
    if slots == "renorm":
        risky_total, unfilled = 1.0, 0
        if k == 0:
            weights["CASH"] = 1.0
    elif slots in ("cashslot", "def"):
        risky_total, unfilled = k / top_n, top_n - k
    else:
        raise ValueError(f"unknown slot mode {slots!r}")
    if k:
        w = {s: risky_total / k for s in selected}
        if sigma is not None:
            sg = {s: sigma(row["signal_session"], s) for s in selected}
            if all(v is not None for v in sg.values()):
                inv = {s: 1.0 / v for s, v in sg.items()}
                tot = sum(inv.values())
                w = {s: risky_total * inv[s] / tot for s in selected}
        for s, v in w.items():
            weights[s] += v
    def_w = 0.0
    if unfilled:
        slot_w = unfilled / top_n
        if slots == "def" and defensive is not None and defensive(row["signal_session"]) == def_symbol:
            weights[def_symbol] += slot_w
            def_w = slot_w
        else:
            weights["CASH"] += slot_w
    return weights, list(selected), def_w


def reselect3(frame: SignalFrame, model_id: str | None = None, *, cap_groups=None, slots: str = "renorm",
              sigma=None, defensive=None, top_n: int = 3) -> SignalFrame:
    rows = []
    for r in frame.rows:
        w, selected, def_w = build_weights(r, cap_groups=cap_groups, slots=slots, sigma=sigma,
                                           defensive=defensive, top_n=top_n)
        row = dict(r)
        row.update(selected_symbols=selected, weights=w, def_ief_weight=def_w)
        rows.append(row)
    return SignalFrame(model_id=model_id or frame.model_id, rows=rows, lineage=frame.lineage)
