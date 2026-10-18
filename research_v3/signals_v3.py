"""Step-2 signal variants (ENS / BUF / IVOL / TRANCHE) producing v2 SignalFrame-compatible rows.

All variants reuse research_v2 helpers (anchors, momentum, trend, select_top_assets, matched monthly).
Rows carry momentum_scores / trend_passes so any variant can be re-selected path-dependently.
"""
from __future__ import annotations

import math
from typing import Callable, Mapping, Sequence

import numpy as np
import pandas as pd

from research_v2.contracts import SignalFrame, UNIVERSE
from research_v2.signals import UNIVERSE_TIE_BREAK, select_top_assets


def combine_momentum(frames: Sequence[SignalFrame], model_id: str, top_n: int = 3) -> SignalFrame:
    """Average momentum_scores across frames (same trend, different lookbacks), re-select via v2.

    A symbol's score is None unless finite in every frame. Only signal sessions present in all frames are kept.
    Row metadata is taken from the last frame (the longest lookback by convention)."""
    by = [{r["signal_session"]: r for r in f.rows} for f in frames]
    common = [s for s in (r["signal_session"] for r in frames[-1].rows) if all(s in b for b in by)]
    rows = []
    for s in common:
        mom = {}
        for sym in UNIVERSE:
            vals = [b[s]["momentum_scores"].get(sym) for b in by]
            ok = all(v is not None and math.isfinite(v) for v in vals)
            mom[sym] = float(np.mean(vals)) if ok else None
        base = by[-1][s]
        sel = select_top_assets(mom, base["trend_passes"], top_n=top_n)
        row = dict(base)
        row.update(eligible_count=len(sel["eligible_symbols"]), eligible_symbols=sel["eligible_symbols"],
                   selected_symbols=sel["selected_symbols"], weights=sel["target_weights"], momentum_scores=mom)
        rows.append(row)
    return SignalFrame(model_id=model_id, rows=rows, lineage=frames[-1].lineage)


def make_sigma_provider(bars: Mapping[str, pd.DataFrame], window: int = 63) -> Callable[[str, str], float | None]:
    """sigma(date, sym) = std(ddof=1) of the last `window` daily adj_close log returns ending at `date`.
    Rolling over past data only, so nothing after `date` is used."""
    table = {}
    for sym in UNIVERSE:
        df = bars[sym]
        px = pd.Series(df["adj_close"].to_numpy(dtype=float), index=df["Date"].astype(str).to_numpy())
        lr = np.log(px).diff()
        sd = lr.rolling(window, min_periods=window).std(ddof=1)
        table[sym] = sd.to_dict()

    def sigma(date: str, sym: str):
        v = table[sym].get(date)
        return None if v is None or not math.isfinite(v) or v <= 0 else float(v)
    return sigma


def _buffered_selection(eligible: list[str], prev: Sequence[str], rank_limit: int, top_n: int) -> list[str]:
    rank = {s: i for i, s in enumerate(eligible, start=1)}
    kept = sorted((s for s in prev if s in rank and rank[s] <= rank_limit), key=lambda s: rank[s])[:top_n]
    for s in eligible:  # eligible is already in v2 order (momentum desc, universe tie-break)
        if len(kept) >= top_n:
            break
        if s not in kept:
            kept.append(s)
    return kept


def reselect(frame: SignalFrame, model_id: str | None = None, *, buffer_rank: int | None = None,
             sigma: Callable[[str, str], float | None] | None = None, top_n: int = 3) -> SignalFrame:
    """Re-run selection/weighting over a row sequence. State (previously held set) is carried across rows."""
    prev: list[str] = []
    rows = []
    for r in frame.rows:
        sel = select_top_assets(r["momentum_scores"], r["trend_passes"], top_n=top_n)
        eligible = sel["eligible_symbols"]
        selected = (_buffered_selection(eligible, prev, buffer_rank, top_n) if buffer_rank is not None
                    else sel["selected_symbols"])
        weights = {s: 0.0 for s in UNIVERSE}
        weights["CASH"] = 0.0
        if not selected:
            weights["CASH"] = 1.0
        else:
            w = {s: 1.0 / len(selected) for s in selected}
            if sigma is not None:
                sig = {s: sigma(r["signal_session"], s) for s in selected}
                if all(v is not None for v in sig.values()):
                    inv = {s: 1.0 / v for s, v in sig.items()}
                    tot = sum(inv.values())
                    w = {s: inv[s] / tot for s in selected}  # risky sum stays 1.0 (v2 equal-weight risky sum)
            weights.update(w)
        prev = list(selected)
        row = dict(r)
        row.update(eligible_count=len(eligible), eligible_symbols=eligible, selected_symbols=list(selected),
                   weights=weights)
        rows.append(row)
    return SignalFrame(model_id=model_id or frame.model_id, rows=rows, lineage=frame.lineage)


def tranche_rows(weekly_rows: Sequence[dict], k: int) -> list[dict]:
    """Rows for sub-portfolio k: the k-th (0-based) weekly event of each calendar month of signal_session,
    last event if the month has fewer."""
    months: dict[tuple[int, int], list[dict]] = {}
    for r in sorted(weekly_rows, key=lambda r: r["signal_session"]):
        ts = pd.Timestamp(r["signal_session"])
        months.setdefault((ts.year, ts.month), []).append(r)
    return [evs[min(k, len(evs) - 1)] for _, evs in sorted(months.items())]


def tranche_frames(weekly_frame: SignalFrame, model_id: str, n_tranches: int = 4, **reselect_kw) -> list[SignalFrame]:
    out = []
    for k in range(n_tranches):
        f = SignalFrame(model_id=f"{model_id}_k{k}", rows=tranche_rows(weekly_frame.rows, k), lineage=[])
        out.append(reselect(f, **reselect_kw))
    return out
