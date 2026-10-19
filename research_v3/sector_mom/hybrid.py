"""HYBRID = frozen CAP_DEF_M allocation layer (cn_transfer, imported) with its equity ETFs replaced by the top-5 S5
(12-1) sector ETFs at E/5 each. Core runner shared by phase 1 (DEV) and the gated phase 2 (hybrid_phase2.py)."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from research_v3.cn_transfer.calendar_cn import (CN_TZ, build_monthly_matched_schedule_cal, build_weekly_schedule_cal,
                                                 get_xshg_calendar, market_as_of_after_close_cal)
from research_v3.cn_transfer.data import DATA_DIR as CN_DIR, load_bars as cn_load_bars
from research_v3.cn_transfer.protocol import load_amendment_verified as cn_amend, load_verified as cn_proto
from research_v3.cn_transfer.runner import build_frames, data_checks, protocol_groups, resolve_missing
from research_v3.cn_transfer.signals_cn import ShiborCurve, cap_def_frame
from research_v3.signals_step3 import make_defensive_provider

from . import guard
from .data import DATA_DIR, load_bars_for_scoring, load_shibor_for_scoring
from .engine import PRIMARY, SENS, ZERO, Plan, StratSpec, simulate
from .panel import Panel
from .protocol_hybrid_loader import load_hybrid_verified
from .universe import load_included

EQUITY = ("510300", "510050", "510500", "159915")
CN_UNIVERSE = ("510300", "510050", "510500", "159915", "518880", "511010")
START = "2020-07-13"
MIN_AGE_S5 = 253
N_SECTOR = 5
COSTS = {"primary": PRIMARY, "sens": SENS, "zero": ZERO}
CAPITAL = 1_000_000.0


def hybrid_weights(cap_w: dict, sector_ranked: list, replace: bool = True) -> dict:
    """Pure weight construction. cap_w: CAP_DEF_M target weights (tickers, may include 'CASH'). Returns ticker weights
    (cash implicit = 1 - sum). Non-equity weights are CAP_DEF_M's exactly."""
    E = float(sum(cap_w.get(t, 0.0) for t in EQUITY))
    out = {t: w for t, w in cap_w.items() if t not in EQUITY and t != "CASH" and w > 0}
    if E > 1e-12:
        top = list(sector_ranked)[:N_SECTOR]
        if replace and top:
            for t in top:
                out[t] = out.get(t, 0.0) + E / len(top)
        else:
            for t in EQUITY:
                if cap_w.get(t, 0.0) > 0:
                    out[t] = cap_w[t]
    return out


def prepare(through: str, log=print) -> dict:
    """Everything needed to simulate; all data is loaded through `through` (guard-checked)."""
    proto = load_hybrid_verified()
    guard.assert_date_allowed(through)
    cnp, amend = cn_proto(), cn_amend()
    assert amend["amendment"] == 1
    cal = get_xshg_calendar()
    universe = tuple(cnp["mapping_from_us_to_cn"]["universe_order"])
    assert universe == CN_UNIVERSE
    groups = protocol_groups(cnp)
    def_symbol = "511010"
    top_n = cnp["rule_unchanged"]["top_n"]
    raw = cn_load_bars(universe, CN_DIR)
    data_start = max(df["Date"].iloc[0] for df in raw.values())
    bars_cn = {s: guard.clip_to_dev(df[(df["Date"] >= data_start) & (df["Date"] <= through)]) for s, df in raw.items()}
    sessions_cn = [x.strftime("%Y-%m-%d") for x in cal.sessions_in_range(data_start, through)]
    rep = data_checks(bars_cn, cal, data_start, through)
    if rep["errors"]:
        raise RuntimeError(f"CN data errors {rep['errors']}")
    vp = Path(CN_DIR) / "tencent_verification_raw.csv"
    ver = pd.read_csv(vp, dtype={"symbol": str}) if vp.is_file() else None
    if ver is not None:
        ver = ver[ver["date"] <= through]
    bars_cn, resolution = resolve_missing(bars_cn, rep["missing_in_data_range"], ver)
    curve = ShiborCurve(load_shibor_for_scoring(DATA_DIR, through=through))
    as_of = market_as_of_after_close_cal(cal, through, CN_TZ)
    weekly = build_weekly_schedule_cal(cal, sessions_cn, as_of)
    monthly = build_monthly_matched_schedule_cal(cal, sessions_cn, as_of, weekly)
    wf, mf = build_frames(cnp, bars_cn, universe, groups, sessions_cn, weekly, monthly, curve, def_symbol)
    defensive = make_defensive_provider(bars_cn, sessions_cn, curve, symbol=def_symbol, months=3)
    capdef = cap_def_frame(mf, "CAP_DEF_M", universe=universe, cap_groups=groups, defensive=defensive, def_symbol=def_symbol, top_n=top_n)
    # ---- sector panel + CN allocation ETFs (cn_transfer adjusted bars) --------------------------------------
    inc = load_included()
    avail = [t for t in inc.ticker if (DATA_DIR / "bars" / f"{t}.csv").exists() and t not in CN_UNIVERSE]
    sbars = load_bars_for_scoring(avail, DATA_DIR, through=through, verify=True)
    sbars = {t: b for t, b in sbars.items() if len(b)}
    meta = inc.set_index("ticker")
    sector = sorted(sbars)
    allbars = dict(sbars)
    for s, df in bars_cn.items():
        allbars[s] = pd.DataFrame({"Date": df["Date"], "open": df["adj_open"], "close": df["adj_close"], "turnover": 0.0})
    first = min(b["Date"].iloc[0] for b in allbars.values())
    sessions = [x.strftime("%Y-%m-%d") for x in cal.sessions_in_range(first, through)]
    limits = {t: float(meta.loc[t, "price_limit"]) for t in sector}
    limits["159915"] = 0.20                                    # ChiNext-tracking ETF
    panel = Panel(allbars, sessions, {t: meta.loc[t, "index_key"] for t in sector}, limits, set(sector))
    return dict(proto=proto, cnp=cnp, cal=cal, panel=panel, curve=curve, capdef=capdef, sector=sector, resolution=resolution,
                cn_report=rep, data_start=data_start, through=through, inc=inc)


def build_plans(ctx: dict, replace: bool = True, start: str = START):
    panel, capdef = ctx["panel"], ctx["capdef"]
    start_pos = panel.pos[start]
    end_pos = len(panel.sessions) - 1
    hyb, cap, rows = Plan(), Plan(), []
    for r in capdef.rows:
        if r["status"] != "COMPLETE":
            continue
        sp, ep = panel.pos[r["signal_session"]], panel.pos[r["execution_session"]]
        if sp < start_pos - 1 or ep > end_pos:
            continue
        capw = {k: v for k, v in r["weights"].items() if k != "CASH" and v > 0}
        E = sum(capw.get(t, 0.0) for t in EQUITY)
        ranked = []
        if E > 1e-12:
            elig = panel.eligible_dedup(sp, MIN_AGE_S5)
            ranked = list(panel.amend_target(sp, elig, 252).keys())
        hw = hybrid_weights(r["weights"], ranked, replace)
        cap.weekly.append((sp, ep, capw))
        hyb.weekly.append((sp, ep, hw))
        rows.append({"signal_date": r["signal_session"], "execution_date": r["execution_session"], "E": E,
                     "n_sector_selected": len(ranked) if E > 1e-12 else 0, "cap_weights": json.dumps(capw), "hybrid_weights": json.dumps(hw)})
    return hyb, cap, pd.DataFrame(rows), start_pos, end_pos


def benchmark_plans(ctx: dict, cap_plan: Plan):
    panel = ctx["panel"]
    bh, c6040, ew = Plan(), Plan(), Plan()
    for k, (sp, ep, _) in enumerate(cap_plan.weekly):
        if k == 0:
            bh.weekly.append((sp, ep, {"510300": 1.0}))
        c6040.weekly.append((sp, ep, {"510300": 0.6, "511010": 0.4}))
        el = panel.eligible_dedup(sp)                   # EW_SECTOR: sector_mom eligibility (250 sessions)
        ew.monthly.append((sp, ep, {panel.tickers[j]: 1.0 / len(el) for j in el} if el else {}))
    return bh, c6040, ew


def groups_of(ctx) -> dict:
    g = {t: "equity" for t in ctx["sector"]}
    g.update({t: "equity" for t in EQUITY})
    g["518880"], g["511010"] = "gold", "bond"
    return g
