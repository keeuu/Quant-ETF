"""DEVELOPMENT-phase run of the pre-registered sector_mom study (score start .. 2024-09-30 only).

The holdout (2024-10-01 .. 2026-09-30) is never loaded: bars are clipped at the loader (guard.DEV_END) and every
scoring entry point asserts the date range. Writes artifacts_dev/.
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from research_v3.cn_transfer.calendar_cn import (CN_TZ, build_weekly_schedule_cal, get_xshg_calendar,
                                                 market_as_of_after_close_cal)
from research_v3.cn_transfer.signals_cn import ShiborCurve
from research_v3.stats import (annual_to_daily_var, bootstrap_sharpe_diff, dsr, psr, sample_moments,
                               stationary_bootstrap_indices, expected_max_sr)

from . import guard
from .data import DATA_DIR, load_bars_for_scoring, load_shibor_for_scoring, verify_manifest
from .engine import PRIMARY, SENS, ZERO, Plan, StratSpec, simulate
from .metrics import calendar_year_table, daily_rf, slice_metrics
from .panel import Panel, TOP_N
from .protocol import load_verified
from .universe import load_included

HERE = Path(__file__).resolve().parent
ART = HERE / "artifacts_dev"
BENCH = "510300"
SPECS = [StratSpec("S1_WEEKLY_MOM", "mom"),
         StratSpec("S2_MA10_EXIT_WEEKLY_REENTRY", "mom", exit_ma=10),
         StratSpec("S3_MA5_EXIT_WEEKLY_REENTRY", "mom", exit_ma=5),
         StratSpec("S4_MA10_EXIT_DAILY_REENTRY", "mom", exit_ma=10, daily_reentry=True),
         StratSpec("CSI300_BH", "bh"),
         StratSpec("EW_SECTOR", "ew")]
STRATS4 = [s.name for s in SPECS[:4]]
COSTS = {"primary": PRIMARY, "sens": SENS, "zero": ZERO}
RUNS = [("primary", False), ("sens", False), ("zero", False), ("primary", True), ("sens", True)]
CAPITAL = {False: 1_000_000.0, True: 100_000.0}
MIN_UNIVERSE = 15


def build_everything(log=print):
    proto = load_verified()
    assert proto["strategies"]["S1_WEEKLY_MOM"] and proto["universe"]["min_universe"]
    inc = load_included()
    man = verify_manifest(DATA_DIR)
    avail = [t for t in inc.ticker if (DATA_DIR / "bars" / f"{t}.csv").exists()]
    missing = [t for t in inc.ticker if t not in avail]
    bars = load_bars_for_scoring(avail + [BENCH], DATA_DIR, verify=False)
    bars = {t: b for t, b in bars.items() if len(b)}
    cal = get_xshg_calendar()
    first = min(b["Date"].iloc[0] for b in bars.values())
    sessions = [x.strftime("%Y-%m-%d") for x in cal.sessions_in_range(first, guard.DEV_END)]
    meta = inc.set_index("ticker")
    sector = [t for t in avail if t in bars]
    panel = Panel({t: bars[t] for t in sector + [BENCH]}, sessions,
                  index_key={t: meta.loc[t, "index_key"] for t in sector},
                  price_limit={t: float(meta.loc[t, "price_limit"]) for t in sector}, sector=set(sector))
    curve = ShiborCurve(load_shibor_for_scoring(DATA_DIR, verify=False))
    as_of = market_as_of_after_close_cal(cal, guard.DEV_END, CN_TZ)
    weekly = build_weekly_schedule_cal(cal, sessions, as_of)
    weekly = [w for w in weekly if w.status == "COMPLETE" and w.execution_session <= guard.DEV_END]
    return proto, inc, man, missing, panel, curve, cal, weekly


def make_plan(panel: Panel, weekly, log=print):
    rows, targets = [], {}
    score_sp = None
    for w in weekly:
        sp, ep = panel.pos[w.signal_session], panel.pos[w.execution_session]
        pre = panel.eligible_pre_dedup(sp)
        post = panel.eligible_dedup(sp)
        rows.append({"signal_date": w.signal_session, "execution_date": w.execution_session,
                     "n_sector_etfs_with_bar": int((panel.is_sector & panel.has_bar[sp]).sum()),
                     "eligible_pre_dedup": len(pre), "eligible_post_dedup": len(post),
                     "min_universe_met": len(post) >= MIN_UNIVERSE})
        if score_sp is None and len(post) >= MIN_UNIVERSE:
            score_sp = sp
        if score_sp is not None:
            targets[sp] = (ep, post)
    if score_sp is None:
        raise RuntimeError("min_universe never met")
    plan = Plan()
    sel_rows = []
    for w in weekly:
        sp = panel.pos[w.signal_session]
        if sp in targets:
            ep, post = targets[sp]
            tg = panel.s1_target(sp, post)
            plan.weekly.append((sp, ep, tg))
            sel_rows.append({"signal_date": w.signal_session, "execution_date": w.execution_session,
                             "n_eligible": len(post), "n_selected": len(tg),
                             "selected": ";".join(f"{k}:{v:.4f}" for k, v in tg.items())})
    start_pos = plan.weekly[0][1]
    # EW: initial allocation at the first scored execution, then first session of each month
    end_pos = len(panel.sessions) - 1
    sp0 = plan.weekly[0][0]
    ew0 = panel.eligible_dedup(sp0)
    plan.monthly.append((sp0, start_pos, {panel.tickers[j]: 1.0 / len(ew0) for j in ew0}))
    for k in range(start_pos + 1, end_pos + 1):
        if panel.sessions[k][:7] != panel.sessions[k - 1][:7]:
            el = panel.eligible_dedup(k - 1)
            if el:
                plan.monthly.append((k - 1, k, {panel.tickers[j]: 1.0 / len(el) for j in el}))
    return plan, pd.DataFrame(rows), pd.DataFrame(sel_rows), start_pos, end_pos


def data_report(panel: Panel, inc: pd.DataFrame, missing: list, out_dir: Path) -> dict:
    info = json.loads((DATA_DIR / "fetch_info.json").read_text())
    rep = {"n_included": int(len(inc)), "n_with_bars": int(panel.is_sector.sum()), "n_without_bars": len(missing),
           "without_bars": missing}
    src = pd.Series({k: v.get("source", "NONE") for k, v in info.items() if k in set(panel.tickers)})
    rep["source_by_symbol_kind"] = src.map(lambda x: x.split("(")[0] if x.startswith("THS_only") else
                                           ("THS+Sina_ext" if x.startswith("THS(") else x)).value_counts().to_dict()
    rep["sina_adjustment_failed"] = sorted(k for k, v in info.items() if v.get("adjustment_failed") or v.get("sina_adjusted") is False)
    rep["ths_empty"] = sorted(k for k, v in info.items() if (v.get("ths_window") or [0, 0, 1])[2] == 0)
    rep["ths_errors"] = {k: v["ths_error"] for k, v in info.items() if v.get("ths_error")}
    rep["sina_errors"] = {k: v["sina_error"] for k, v in info.items() if v.get("sina_error")}
    q = lambda key: [float(x) for x in np.nanpercentile([v[key] for v in info.values() if key in v], [50, 95, 100])] if any(key in v for v in info.values()) else None
    rep["overlap_ratio_max_dev_p50_p95_max"] = q("overlap_ratio_max_dev")
    rep["overlap_daily_logret_max_abs_diff_p50_p95_max"] = q("overlap_daily_logret_max_abs_diff")
    rep["overlap_turnover_median_ratio_p50_p95_max"] = q("overlap_turnover_median_ratio")
    rep["ths_dates_missing_in_sina_total"] = int(sum(v.get("ths_dates_missing_in_sina", 0) for v in info.values()))
    miss, gaps = {}, {}
    for t in panel.tickers:
        j = panel.col[t]
        has = panel.has_bar[:, j]
        if not has.any():
            continue
        f = int(np.argmax(has)); l = len(has) - 1 - int(np.argmax(has[::-1]))
        m = int((~has[f:l + 1]).sum())
        if m:
            miss[t] = m
    rep["missing_session_bars_inside_listing_span"] = {"n_symbols": len(miss), "total": int(sum(miss.values())),
                                                        "worst": sorted(miss.items(), key=lambda kv: -kv[1])[:10]}
    rep["bars_on_non_session_dates"] = {k: v for k, v in panel.off_session_bars.items() if v}
    (out_dir / "data_report.json").write_text(json.dumps(rep, indent=1, default=str))
    return rep


def periods(start: str, end: str):
    mid = pd.Timestamp(start) + (pd.Timestamp(end) - pd.Timestamp(start)) / 2
    mid_s = mid.strftime("%Y-%m-%d")
    return {"DEV": (start, end), "DEV_A": (start, mid_s), "DEV_B": ((pd.Timestamp(mid_s) + pd.Timedelta(days=1)).strftime("%Y-%m-%d"), end)}, mid_s


def select_candidate(m: pd.DataFrame) -> dict:
    """Mechanical application of candidate_selection_on_development_only. m: rows of the fractional-lot runs with
    columns cost, strategy, period, xsharpe, max_drawdown."""
    g = lambda cost, strat, per, col: float(m[(m.cost == cost) & (m.strategy == strat) & (m.period == per)][col].iloc[0])
    detail = {}
    for s in STRATS4:
        d = {}
        for cost in ("primary", "sens"):
            d[cost] = {
                "xs_DEV": g(cost, s, "DEV", "xsharpe"), "xs_DEV_EW": g(cost, "EW_SECTOR", "DEV", "xsharpe"),
                "xs_DEV_CSI": g(cost, "CSI300_BH", "DEV", "xsharpe"),
                "mdd_DEV": g(cost, s, "DEV", "max_drawdown"), "mdd_DEV_CSI": g(cost, "CSI300_BH", "DEV", "max_drawdown"),
                "xs_A": g(cost, s, "DEV_A", "xsharpe"), "xs_A_EW": g(cost, "EW_SECTOR", "DEV_A", "xsharpe"),
                "xs_B": g(cost, s, "DEV_B", "xsharpe"), "xs_B_EW": g(cost, "EW_SECTOR", "DEV_B", "xsharpe")}
            x = d[cost]
            x["C1_xs_gt_EW_and_CSI"] = bool(x["xs_DEV"] > x["xs_DEV_EW"] and x["xs_DEV"] > x["xs_DEV_CSI"])
            x["C2_mdd_lt_CSI"] = bool(x["mdd_DEV"] < x["mdd_DEV_CSI"])
            x["C3_xs_gt_EW_in_A_and_B"] = bool(x["xs_A"] > x["xs_A_EW"] and x["xs_B"] > x["xs_B_EW"])
            x["all"] = bool(x["C1_xs_gt_EW_and_CSI"] and x["C2_mdd_lt_CSI"] and x["C3_xs_gt_EW_in_A_and_B"])
        d["qualifies"] = bool(d["primary"]["all"] and d["sens"]["all"])
        d["min_xs_A_B_primary"] = min(d["primary"]["xs_A"], d["primary"]["xs_B"])
        detail[s] = d
    q = [s for s in STRATS4 if detail[s]["qualifies"]]
    pick = max(q, key=lambda s: detail[s]["min_xs_A_B_primary"]) if q else None
    return {"candidate": pick, "qualifiers": q, "detail": detail}


def run(out_dir: Path = ART, log=print) -> dict:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    proto, inc, man, missing, panel, curve, cal, weekly = build_everything(log)
    plan, uot, sel, start_pos, end_pos = make_plan(panel, weekly, log)
    start, end = panel.sessions[start_pos], panel.sessions[end_pos]
    pers, mid = periods(start, end)
    log(f"universe: included={len(inc)} with-data={int(panel.is_sector.sum())} no-data={len(missing)}; "
        f"score start exec {start} (signal {panel.sessions[plan.weekly[0][0]]}); end {end}; mid {mid}")
    drep = data_report(panel, inc, missing, out_dir)
    uot.to_csv(out_dir / "universe_over_time.csv", index=False)
    sel.to_csv(out_dir / "signals_S1_weekly_targets.csv", index=False)
    dates = panel.sessions[start_pos:end_pos + 1]
    rf = daily_rf(dates, curve)
    mrows, cyrows, results, ex_series = [], [], {}, {}
    for cname, lots in RUNS:
        cap = CAPITAL[lots]
        for spec in SPECS:
            res = simulate(panel, plan, spec, COSTS[cname], cap, lots, curve, start_pos, end_pos)
            results[(cname, lots, spec.name)] = res
            assert len(res.nav) == len(dates)
            for per, (a, b) in pers.items():
                mt, ex = slice_metrics(res, rf, a, b, cap, include_counters=(per == "DEV"))
                ex_series[(cname, lots, spec.name, per)] = ex
                mrows.append({"cost": cname, "lot_mode": "lots100k" if lots else "fractional1m", "strategy": spec.name,
                              "period": per, **mt})
            yt = calendar_year_table(res.nav)
            for y, r in yt.iterrows():
                cyrows.append({"cost": cname, "lot_mode": "lots100k" if lots else "fractional1m", "strategy": spec.name,
                               "year": y, "return": r["ret"], "sessions": r["sessions"], "full_year": r["full_year"]})
        log(f"ran {cname} lots={lots}")
    M = pd.DataFrame(mrows)
    # cost drag (DEV): zero-cost CAGR minus cost CAGR, fractional mode
    z = M[(M.cost == "zero") & (M.period == "DEV")].set_index("strategy")["cagr"]
    M["cost_drag"] = np.nan
    for cname in ("primary", "sens"):
        for s in z.index:
            m = (M.cost == cname) & (M.lot_mode == "fractional1m") & (M.strategy == s)
            M.loc[m, "cost_drag"] = z[s] - M.loc[m, "cagr"]
    long = M.melt(id_vars=["cost", "lot_mode", "strategy", "period"], var_name="metric", value_name="value")
    long.to_csv(out_dir / "metrics_long.csv", index=False)
    M.to_csv(out_dir / "metrics_wide.csv", index=False)
    pd.DataFrame(cyrows).to_csv(out_dir / "calendar_year_returns.csv", index=False)
    # trade logs (primary cost, fractional)
    for spec in SPECS:
        r = results[("primary", False, spec.name)]
        pd.DataFrame(r.trades).to_csv(out_dir / f"trades_{spec.name}.csv", index=False)
        r.nav.to_csv(out_dir / f"nav_{spec.name}.csv", index=False)
    # bootstrap (primary, fractional): excess-Sharpe difference vs EW_SECTOR and CSI300_BH
    seed, nres, blk = 20261019, 2000, 21
    b_rows = []
    for per in pers:
        a0 = ex_series[("primary", False, STRATS4[0], per)]
        idx = stationary_bootstrap_indices(len(a0), blk, nres, seed)
        for s in STRATS4:
            a = ex_series[("primary", False, s, per)]
            for ref in ("EW_SECTOR", "CSI300_BH"):
                pt, lo, hi = bootstrap_sharpe_diff(a, ex_series[("primary", False, ref, per)], idx, 0.90)
                b_rows.append({"period": per, "strategy": s, "reference": ref, "xsharpe_diff": pt, "ci90_lo": lo,
                               "ci90_hi": hi, "ci_includes_0": bool(lo <= 0 <= hi), "block": blk, "n_resamples": nres,
                               "seed": seed})
    BT = pd.DataFrame(b_rows)
    BT.to_csv(out_dir / "bootstrap.csv", index=False)
    # PSR / DSR (DEV, N = 4 strategies)
    p_rows = []
    for cname in ("primary", "sens"):
        daily_sr = [sample_moments(ex_series[(cname, False, s, "DEV")])[0] for s in STRATS4]
        var_d = float(np.var(daily_sr, ddof=1))
        for s in STRATS4 + ["EW_SECTOR", "CSI300_BH"]:
            ex = ex_series[(cname, False, s, "DEV")]
            p_rows.append({"cost": cname, "strategy": s, "psr0_excess": psr(ex, 0.0),
                           "dsr_N4": dsr(ex, 4, var_d) if s in STRATS4 else float("nan"),
                           "sr0_daily": expected_max_sr(var_d, 4), "sr0_annual": expected_max_sr(var_d, 4) * math.sqrt(252),
                           "var_daily_sr_across_4": var_d})
    PD = pd.DataFrame(p_rows)
    PD.to_csv(out_dir / "psr_dsr.csv", index=False)
    sel_res = select_candidate(M[M.lot_mode == "fractional1m"])
    (out_dir / "selection.json").write_text(json.dumps(sel_res, indent=2))
    ctx = dict(drep=drep, proto=proto, inc=inc, man=man, missing=missing, panel=panel, uot=uot, M=M, BT=BT, PD=PD, sel=sel_res,
               start=start, end=end, mid=mid, pers=pers, cy=pd.DataFrame(cyrows), plan=plan, results=results,
               protocol_sha=(HERE / "protocol_sector_mom.sha256").read_text().strip())
    from .report import write_summary
    write_summary(out_dir, ctx)
    return ctx


if __name__ == "__main__":
    run()
