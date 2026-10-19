"""HYBRID phase 2: the ONE-TIME holdout run (2024-10-01 .. 2026-09-30), scored continuously from the DEV start.

Gates (all checked before any data is read):
  1. HOLDOUT_AUTHORIZATION.json must exist in research_v3/sector_mom/ (written by the main session after the user's go-ahead;
     minimal content: {"authorized": true, ...}).
  2. holdout_state_hybrid.json must NOT exist; it is written (status "started") before the run, so a second run - or a rerun
     after a crash - is refused.
  3. The guard override (guard.open_holdout) is the only path that may lift the sector_mom holdout guard.
This module is never imported by the DEV scripts and is not executed in phase 1.
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from . import guard

HERE = Path(__file__).resolve().parent
AUTH_PATH = HERE / "HOLDOUT_AUTHORIZATION.json"
STATE_PATH = HERE / "holdout_state_hybrid.json"
OUT_DIR = HERE / "artifacts_hybrid_holdout"


class Phase2Refused(RuntimeError):
    pass


def check_gate(auth_path: Path = AUTH_PATH, state_path: Path = STATE_PATH) -> dict:
    auth_path, state_path = Path(auth_path), Path(state_path)
    if not auth_path.is_file():
        raise Phase2Refused(f"holdout not authorised: {auth_path.name} is missing")
    try:
        auth = json.loads(auth_path.read_text())
    except Exception as e:  # noqa: BLE001
        raise Phase2Refused(f"authorisation file unreadable: {e}") from e
    if not isinstance(auth, dict) or auth.get("authorized") is not True:
        raise Phase2Refused('authorisation file must be a JSON object with "authorized": true')
    if state_path.exists():
        raise Phase2Refused(f"holdout already run (or started): {state_path.name} exists; a second phase-2 run is refused")
    return auth


def run_phase2(auth_path: Path = AUTH_PATH, state_path: Path = STATE_PATH, out_dir: Path = OUT_DIR, core=None) -> dict:
    auth = check_gate(auth_path, state_path)
    if core is None:
        if not hasattr(guard, "open_holdout"):
            raise Phase2Refused("guard override (guard.open_holdout) is not installed; phase 2 cannot lift the holdout guard")
        core = _score_holdout
    state_path = Path(state_path)
    state_path.write_text(json.dumps({"status": "started", "started_utc": pd.Timestamp.now(tz="UTC").isoformat(),
                                      "authorization": auth}, indent=1))
    result = core(Path(out_dir))
    state_path.write_text(json.dumps({"status": "completed", "completed_utc": pd.Timestamp.now(tz="UTC").isoformat(),
                                      "authorization": auth}, indent=1))
    return result


def classify_outcome(prim: dict, sens: dict) -> dict:
    """prim/sens: {'xs_h','xs_c','mdd_h','mdd_c'} (max drawdown as positive depth)."""
    ok = lambda d: bool(d["xs_h"] > d["xs_c"] and d["mdd_h"] <= d["mdd_c"] + 0.05)
    p, s = ok(prim), ok(sens)
    return {"primary_cost_met": p, "sensitivity_cost_met": s,
            "outcome": "SUPPORTED" if (p and s) else ("WEAK" if p else "NOT_SUPPORTED")}


def _score_holdout(out_dir: Path) -> dict:
    import numpy as np
    from research_v3.stats import bootstrap_sharpe_diff, stationary_bootstrap_indices
    from .hybrid import CAPITAL, prepare
    from .metrics import calendar_year_table, daily_rf, slice_metrics
    from .run_hybrid_dev import STRATS, simulate_all
    out_dir.mkdir(parents=True, exist_ok=True)
    with guard.open_holdout():
        ctx = prepare(guard.HOLDOUT_END)
        res = {cn: simulate_all(ctx, cn) for cn in ("primary", "sens", "zero")}
        panel = ctx["panel"]
        _, _, start_pos, end_pos = res["primary"][0], None, res["primary"][2], res["primary"][3]
        dates = panel.sessions[start_pos:end_pos + 1]
        rf = daily_rf(dates, ctx["curve"])
        h0 = next(d for d in dates if d >= guard.HOLDOUT_START)
        h1 = dates[-1]
        rows, ex = [], {}
        for cn, (rr, _, _, _) in res.items():
            for s in STRATS:
                mt, e = slice_metrics(rr[s], rf, h0, h1, CAPITAL, include_counters=True)
                ex[(cn, s)] = e
                gw = rr[s].group_w
                m = (rr[s].nav["date"] >= h0).to_numpy()
                mt.update(mean_equity=float(gw.equity[m].mean()), mean_gold=float(gw.gold[m].mean()), mean_bond=float(gw.bond[m].mean()))
                rows.append({"cost": cn, "slice": "HOLDOUT", "strategy": s, **mt})
        M = pd.DataFrame(rows)
        for cn in ("primary", "sens"):
            for s in STRATS:
                z = M[(M.cost == "zero") & (M.strategy == s)].cagr.iloc[0]
                mm = (M.cost == cn) & (M.strategy == s)
                M.loc[mm, "cost_drag"] = z - M.loc[mm, "cagr"]
        M.to_csv(out_dir / "metrics_holdout.csv", index=False)
        g = lambda cn, s, c: float(M[(M.cost == cn) & (M.strategy == s)][c].iloc[0])
        crit = classify_outcome(
            {"xs_h": g("primary", "HYBRID", "xsharpe"), "xs_c": g("primary", "CAP_DEF_M_CN", "xsharpe"),
             "mdd_h": g("primary", "HYBRID", "max_drawdown"), "mdd_c": g("primary", "CAP_DEF_M_CN", "max_drawdown")},
            {"xs_h": g("sens", "HYBRID", "xsharpe"), "xs_c": g("sens", "CAP_DEF_M_CN", "xsharpe"),
             "mdd_h": g("sens", "HYBRID", "max_drawdown"), "mdd_c": g("sens", "CAP_DEF_M_CN", "max_drawdown")})
        idx = stationary_bootstrap_indices(len(ex[("primary", "HYBRID")]), 21, 2000, 20261019)
        pt, lo, hi = bootstrap_sharpe_diff(ex[("primary", "HYBRID")], ex[("primary", "CAP_DEF_M_CN")], idx, 0.90)
        crit["bootstrap_primary"] = {"diff": pt, "lo": lo, "hi": hi, "includes_0": bool(lo <= 0 <= hi)}
        crit["slice"] = [h0, h1]
        (out_dir / "verdict.json").write_text(json.dumps(crit, indent=1))
        for s, r in res["primary"][0].items():
            pd.DataFrame(r.trades).to_csv(out_dir / f"trades_{s}.csv", index=False)
        return crit
