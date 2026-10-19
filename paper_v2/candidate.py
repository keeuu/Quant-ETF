"""Frozen research_v3 candidate CAP_DEF_M as a paper-only shadow model.

The signal is produced by the very research functions used in research_v3/step3.py variant_frames
(name CAP_DEF, cadence M); nothing is reimplemented here. All parameters come from
research_v3/candidates_frozen.json (label CAP_DEF_M).
"""
import hashlib
import json
import os
import sys
from typing import Any, Dict, List, Optional

from research_v2.schedule import build_monthly_matched_schedule, build_weekly_schedule
from research_v2.signals import compute_matched_monthly_signals, compute_model_signals_for_events
from research_v3.cash_yield import CashYieldCurve
from research_v3.signals_step3 import make_defensive_provider, reselect3

from .month_end import true_month_ends
from .config import (CANDIDATE_COST_BPS, CANDIDATE_EFFECTIVE_COST_BPS, CANDIDATE_LABEL, CANDIDATE_MODEL,
                     CANDIDATE_SLIPPAGE_BPS, FREEZE_DATE)

FROZEN_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "research_v3", "candidates_frozen.json"))


def candidates_frozen_sha256() -> str:
    with open(FROZEN_PATH, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def load_frozen() -> Dict[str, Any]:
    with open(FROZEN_PATH, "rb") as f:
        return json.loads(f.read().decode("utf-8"))


def candidate_spec(label: str = CANDIDATE_LABEL) -> Dict[str, Any]:
    """The frozen candidate entry; cross-checks it against config.CANDIDATE_MODEL / cost settings."""
    frozen = load_frozen()
    matches = [c for c in frozen["candidates"] if c["label"] == label]
    if len(matches) != 1:
        raise RuntimeError(f"candidate {label!r} not found exactly once in candidates_frozen.json")
    c = matches[0]
    st = c["settings"]
    ok = (c["cadence"] == CANDIDATE_MODEL.cadence and c["trend_weeks"] == CANDIDATE_MODEL.trend_weeks
          and c["base_momentum_months"] == CANDIDATE_MODEL.momentum_months and c["variant"] == "CAP_DEF"
          and st["cost_bps"] == CANDIDATE_COST_BPS and st["slippage_bps"] == CANDIDATE_SLIPPAGE_BPS
          and st["cash_yield_source"] == "irx" and frozen["freeze_date"] == FREEZE_DATE)
    if not ok:
        raise RuntimeError("paper_v2.config candidate constants disagree with candidates_frozen.json")
    return c


def provenance(market_date: str, irx_data_hash: Optional[str] = None, commit: Optional[str] = None) -> Dict[str, Any]:
    """Provenance fields for every CAP_DEF_M journal entry. market_date: signal_session for signals,
    execution/accrual/nav date otherwise. evaluation_eligible only for dates strictly after the freeze date."""
    from .runinfo import strategy_commit
    frozen = load_frozen()
    return {
        "candidates_frozen_sha256": candidates_frozen_sha256(),
        "candidate_code_commit": frozen["code_git_commit"],
        "strategy_commit": commit or strategy_commit(),
        "freeze_date": frozen["freeze_date"],
        "evaluation_eligible": bool(market_date > frozen["freeze_date"]),
        "irx_data_hash": irx_data_hash,
        "cost_bps": CANDIDATE_COST_BPS,
        "slippage_bps": CANDIDATE_SLIPPAGE_BPS,
    }


def cap_def_m_signal_row(bars, session_dates: List[str], as_of_ts: str, curve: CashYieldCurve) -> Optional[Dict[str, Any]]:
    """Signal row (weights incl. CASH, selected_symbols, def_ief_weight, signal_session, execution_session,
    ...) of the latest completed matched-monthly event as of `as_of_ts`; None if there is no such row.

    `bars` must be in research_v2 format (Date column). Mirrors research_v3.step3.variant_frames for
    variant CAP_DEF, cadence M, with parameters read from candidates_frozen.json.
    """
    c = candidate_spec()
    weekly_events = build_weekly_schedule(session_dates, as_of_ts)
    if not weekly_events:
        return None
    monthly_events = true_month_ends(build_monthly_matched_schedule(session_dates, as_of_ts, weekly_events))
    if not monthly_events:
        return None
    latest = monthly_events[-1]
    m, t = c["base_momentum_months"], c["trend_weeks"]
    weekly = compute_model_signals_for_events(bars, session_dates, weekly_events, f"W_M{m:02d}_T{t}", m, t)
    monthly = compute_matched_monthly_signals(weekly, [latest], f"M_M{m:02d}_T{t}")
    dt = c["defensive_test"]
    defensive = make_defensive_provider(bars, session_dates, curve, dt["symbol"], dt["months"])
    frame = reselect3(monthly, f"M_CAP_DEF_T{t}", cap_groups=c["asset_class_groups"], slots=c["variant_spec"]["slots"],
                      sigma=None, defensive=defensive, top_n=c["top_n"])
    for row in frame.rows:
        if row["signal_session"] == latest.signal_session:
            return row
    return None


def warn(msg: str) -> None:
    print(f"WARNING: {CANDIDATE_LABEL}: {msg}", file=sys.stderr)
