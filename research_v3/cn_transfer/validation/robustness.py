"""Part B robustness checks B1-B5 (protocol_cn_validation.json). Descriptive: nothing is selected from them."""
from __future__ import annotations

import bisect
import json
from pathlib import Path

import numpy as np
import pandas as pd

from research_v3.config import V3Config
from research_v3.step3 import calendar_year_returns

from .. import runner as R
from .variants import LotCosts, run_variant
from .world import World, benchmark_frames, build_world

PRIMARY = V3Config(cost_bps=5.0, slippage_bps=5.0, cash_yield_source="irx")
STRATS = ["CAP_DEF_M", "CSI300_BH", "CN_60_40"]


# ------------------------------------------------------------------ evaluation helpers
def evaluate(world: World, frames: dict, cfg: V3Config, score_start: str, lag: int = 0, lots: LotCosts | None = None,
             capital: float | None = None):
    """Run each frame, return ({strategy: {partition: metrics row}}, {strategy: (nav, reb)})."""
    cfg = cfg if capital is None else V3Config(cost_bps=cfg.cost_bps, slippage_bps=cfg.slippage_bps,
                                               cash_yield_source=cfg.cash_yield_source, initial_nav=capital)
    bounds = R.partition_bounds(score_start, world.score_end)
    res, raw = {}, {}
    for name, fr in frames.items():
        nav, reb, _m = run_variant(world.cal, world.bars, score_start, world.score_end, fr, cfg, world.curve,
                                   world.universe, world.sessions, lag=lag, lots=lots)
        rf = R.daily_rf(nav["date"].tolist(), world.curve)
        nav["rf_accrual"] = rf
        yt = calendar_year_returns(nav, cfg.initial_nav)
        res[name] = {}
        for part, (a, b) in bounds.items():
            _ex, row = R.metrics_for(world.cal, nav, reb, fr, rf, part, a, b, cfg.initial_nav, yt)
            res[name][part] = row
        raw[name] = (nav, reb)
    return res, raw


def cell_check(strat: dict, bench: dict) -> dict:
    """Per-cell check of B1: FULL xSharpe(strategy) > CN_60_40 FULL xSharpe AND FULL MDD(strategy) < CSI300_BH FULL MDD."""
    s = strat["FULL"]
    xs = s["excess_sharpe"] > bench["CN_60_40"]["FULL"]["excess_sharpe"]
    dd = s["max_drawdown"] < bench["CSI300_BH"]["FULL"]["max_drawdown"]
    return {"xsharpe_cond": bool(xs), "mdd_cond": bool(dd), "pass": bool(xs and dd)}


def _row(label: dict, strat: dict, chk: dict, extra: dict | None = None) -> dict:
    f = strat["FULL"]
    out = {**label, "cagr": f["cagr"], "ann_vol": f["ann_vol"], "xsharpe": f["excess_sharpe"],
           "mdd": f["max_drawdown"], "mean_cash_weight": f["mean_cash_weight"], "rebalances": f["rebalance_count"],
           "turnover": f["ann_one_way_turnover"], "total_fees": f["total_fees"], "final_nav": f["final_nav"],
           "xsharpe_sub_a": strat["SUB_A"]["excess_sharpe"], "xsharpe_sub_b": strat["SUB_B"]["excess_sharpe"],
           **{f"chk_{k}": v for k, v in chk.items()}}
    if extra:
        out.update(extra)
    return out


# ------------------------------------------------------------------ B1 / B2 / B3 / B4
def baseline_frames(world: World, score_start: str, m=12, t=40):
    mf, capdef = world.frames(m, t)
    bm = benchmark_frames(world, mf, score_start, "510300", "511010")
    return {"CAP_DEF_M": capdef, **bm}


def run_b0(world: World, score_start: str):
    fr = baseline_frames(world, score_start)
    return evaluate(world, fr, PRIMARY, score_start)


def run_b1(world: World, score_start: str, bench: dict, proto: dict) -> pd.DataFrame:
    g = proto["part_B_robustness"]["B1_parameter_neighbourhood"]["grid"]
    rows = []
    for m in g["momentum_months"]:
        for t in g["trend_weeks"]:
            _, capdef = world.frames(m, t)
            res, _ = evaluate(world, {"CAP_DEF_M": capdef}, PRIMARY, score_start)
            chk = cell_check(res["CAP_DEF_M"], bench)
            rows.append(_row({"momentum_months": m, "trend_weeks": t}, res["CAP_DEF_M"], chk))
    return pd.DataFrame(rows)


def run_b2(world: World, score_start: str, bench_base: dict, proto: dict) -> pd.DataFrame:
    mf, capdef = world.frames(12, 40)
    bm = benchmark_frames(world, mf, score_start, "510300", "511010")
    fr = {"CAP_DEF_M": capdef, **bm}
    rows = []
    for k in proto["part_B_robustness"]["B2_execution_lag"]["offsets_sessions"]:
        res, _ = evaluate(world, fr, PRIMARY, score_start, lag=k)
        chk = cell_check(res["CAP_DEF_M"], res)                    # primary: benchmarks lagged by the same k
        chk_alt = cell_check(res["CAP_DEF_M"], bench_base)         # sensitivity: baseline (unlagged) benchmarks
        rows.append(_row({"lag_sessions": k}, res["CAP_DEF_M"], chk,
                         {"bench_CN_60_40_xsharpe": res["CN_60_40"]["FULL"]["excess_sharpe"],
                          "bench_CSI300_BH_mdd": res["CSI300_BH"]["FULL"]["max_drawdown"],
                          "alt_pass_vs_unlagged_benchmarks": chk_alt["pass"]}))
    return pd.DataFrame(rows)


def substituted(universe, groups, mapping: dict):
    u = tuple(mapping.get(s, s) for s in universe)
    g = {k: [mapping.get(s, s) for s in v] for k, v in groups.items()}
    return u, g


def run_b3(base_world: World, score_start: str, bench_base: dict, proto: dict, val_dir=None):
    from .fetch import VDATA
    sub = proto["part_B_robustness"]["B3_etf_substitution"]["substitutions"]
    variants = {"510300->510330": {"510300": sub["510300"]}, "510500->159922": {"510500": sub["510500"]},
                "both": dict(sub)}
    rows, notes = [], []
    for name, mp in variants.items():
        needed = list(mp.values())
        ok = True
        for s in needed:
            f = Path(val_dir or VDATA) / f"{s}.csv"
            if not f.is_file():
                ok = False
                notes.append(f"{name}: NOT RUN, no adjusted history for {s} (download/adjustment failed or missing)")
                continue
            first = pd.read_csv(f, dtype={"Date": str})["Date"].iloc[0]
            if first > base_world.data_start:
                ok = False
                notes.append(f"{name}: NOT RUN, {s} history starts {first} > {base_world.data_start}")
        if not ok:
            rows.append({"variant": name, "run": "NOT RUN"})
            continue
        u, g = substituted(base_world.universe, base_world.groups, mp)
        w = build_world(u, g)
        v_start = w.first_warm_start()
        start = score_start if (v_start is not None and v_start <= score_start) else v_start
        if start != score_start:
            notes.append(f"{name}: variant warm-up later ({v_start}); scored from {start} instead of {score_start}")
        mf, capdef = w.frames(12, 40)
        csi = u[0]
        bm_sub = benchmark_frames(w, mf, start, csi, "511010")
        res, _ = evaluate(w, {"CAP_DEF_M": capdef, **bm_sub}, PRIMARY, start)
        chk = cell_check(res["CAP_DEF_M"], bench_base)             # primary: baseline benchmarks (fixed references)
        chk_alt = cell_check(res["CAP_DEF_M"], res)                # sensitivity: benchmarks on the substituted CSI300 ETF
        missing = {s: len(v) for s, v in w.report["missing_in_data_range"].items() if v}
        rows.append(_row({"variant": name, "run": "RUN", "score_start": start}, res["CAP_DEF_M"], chk,
                         {"alt_pass_vs_substituted_benchmarks": chk_alt["pass"],
                          "sub_bench_CN_60_40_xsharpe": res["CN_60_40"]["FULL"]["excess_sharpe"],
                          "sub_bench_CSI300_BH_mdd": res["CSI300_BH"]["FULL"]["max_drawdown"],
                          "missing_bars_in_data_range": json.dumps(missing)}))
    return pd.DataFrame(rows), notes


def run_b4(world: World, score_start: str, base_res: dict, bench_base: dict, proto: dict) -> pd.DataFrame:
    spec = proto["part_B_robustness"]["B4_small_account_execution"]
    lots = LotCosts(lot=spec["lot_size_shares"], commission_rate=2.5e-4, min_commission=5.0, slippage_bps=5.0)
    fr = baseline_frames(world, score_start)
    rows = []
    base = base_res["CAP_DEF_M"]["FULL"]
    rows.append({"account": "baseline (fractional, 5+5 bps)", "capital": 100000, "cagr": base["cagr"],
                 "xsharpe": base["excess_sharpe"], "mdd": base["max_drawdown"],
                 "mean_cash_weight": base["mean_cash_weight"], "rebalances": base["rebalance_count"],
                 "total_fees": base["total_fees"], "cagr_drop_pp": 0.0})
    for cap in spec["capital_cny"]:
        res, _ = evaluate(world, fr, PRIMARY, score_start, lots=lots, capital=float(cap))
        chk = cell_check(res["CAP_DEF_M"], res)                    # primary: benchmarks under the same account rules
        chk_alt = cell_check(res["CAP_DEF_M"], bench_base)         # sensitivity: fractional baseline benchmarks
        f = res["CAP_DEF_M"]["FULL"]
        drop = (base["cagr"] - f["cagr"]) * 100
        rows.append({"account": f"lots, {cap} CNY", "capital": cap, "cagr": f["cagr"], "xsharpe": f["excess_sharpe"],
                     "mdd": f["max_drawdown"], "mean_cash_weight": f["mean_cash_weight"],
                     "rebalances": f["rebalance_count"], "total_fees": f["total_fees"], "cagr_drop_pp": drop,
                     "bench_CN_60_40_xsharpe": res["CN_60_40"]["FULL"]["excess_sharpe"],
                     "bench_CSI300_BH_mdd": res["CSI300_BH"]["FULL"]["max_drawdown"],
                     **{f"chk_{k}": v for k, v in chk.items()}, "alt_pass_vs_fractional_benchmarks": chk_alt["pass"],
                     "holds_condition": bool(drop <= 1.0 and chk["pass"]) if cap == 100000 else None})
    # sensitivity, NOT part of the protocol: same lot rounding but commission 5 bp (min 5 CNY) so the cost schedule equals the
    # baseline's 5+5 bps for large trades and only rounding / minimum-fee effects remain
    lots5 = LotCosts(lot=spec["lot_size_shares"], commission_rate=5e-4, min_commission=5.0, slippage_bps=5.0)
    for cap in spec["capital_cny"]:
        res, _ = evaluate(world, fr, PRIMARY, score_start, lots=lots5, capital=float(cap))
        f = res["CAP_DEF_M"]["FULL"]
        chk = cell_check(res["CAP_DEF_M"], res)
        rows.append({"account": f"SENSITIVITY lots, {cap} CNY, commission 5bp (min 5)", "capital": cap, "cagr": f["cagr"],
                     "xsharpe": f["excess_sharpe"], "mdd": f["max_drawdown"], "mean_cash_weight": f["mean_cash_weight"],
                     "rebalances": f["rebalance_count"], "total_fees": f["total_fees"],
                     "cagr_drop_pp": (base["cagr"] - f["cagr"]) * 100, "chk_pass": chk["pass"]})
    return pd.DataFrame(rows)


# ------------------------------------------------------------------ B5
def max_drawdown_episode(nav: pd.DataFrame, initial_nav: float) -> dict:
    """Peak / trough / recovery of the FULL-run max drawdown, same path convention as compute_backtest_metrics
    (initial capital is the first point of the path)."""
    vals = np.concatenate([[initial_nav], nav["nav"].to_numpy(float)])
    dates = ["INITIAL"] + nav["date"].tolist()
    runmax = np.maximum.accumulate(vals)
    dd = (runmax - vals) / runmax
    t = int(np.argmax(dd))
    peak_val = runmax[t]
    p = max(i for i in range(t + 1) if vals[i] == peak_val)
    rec = next((i for i in range(t + 1, len(vals)) if vals[i] >= peak_val), None)
    return {"mdd": float(dd[t]), "peak_date": dates[p], "peak_nav": float(peak_val), "trough_date": dates[t],
            "trough_nav": float(vals[t]), "recovery_date": None if rec is None else dates[rec],
            "peak_idx": p, "trough_idx": t, "recovery_idx": rec,
            "sessions_peak_to_trough": t - p, "sessions_trough_to_recovery": None if rec is None else rec - t}


def daily_contributions(world: World, nav: pd.DataFrame, reb: pd.DataFrame, capdef, initial_nav: float) -> pd.DataFrame:
    """Per-day P&L by holding reconstructed from the ledger (shares = w * V_post / open), carry-forward valuation as the
    engine. Columns: date, one per symbol, cash_interest, fee, total, nav_change (and recon error)."""
    U = world.universe
    wmap = {r["signal_session"]: r["weights"] for r in capdef.rows}
    rebd = {r.execution_session: r for r in reb.itertuples()}
    op = {s: dict(zip(world.bars[s]["Date"], world.bars[s]["adj_open"])) for s in U}
    cl = {s: dict(zip(world.bars[s]["Date"], world.bars[s]["adj_close"])) for s in U}
    q = {s: 0.0 for s in U}
    last = {}
    out, prev_nav = [], initial_nav
    for rec in nav.itertuples():
        d = rec.date
        row = {"date": d}
        pnl = {s: 0.0 for s in U}
        if d in rebd:
            rb = rebd[d]
            for s in U:           # old holdings: previous close -> open
                if q[s] > 1e-10 and s in last and d in op[s]:
                    pnl[s] += q[s] * (op[s][d] - last[s])
            w = wmap[rb.signal_session]
            q = {s: (w.get(s, 0.0) * rb.V_post / op[s][d] if w.get(s, 0.0) > 1e-10 else 0.0) for s in U}
            for s in U:
                if q[s] > 1e-10:
                    pnl[s] += q[s] * (cl[s].get(d, last.get(s)) - op[s][d])
        else:
            for s in U:
                if q[s] > 1e-10:
                    pnl[s] += q[s] * (cl[s].get(d, last[s]) - last[s])
        for s in U:
            if d in cl[s]:
                last[s] = cl[s][d]
        row.update(pnl)
        row["cash_interest"] = rec.cash_interest
        row["fee"] = -rec.fee
        tot = sum(pnl.values()) + rec.cash_interest - rec.fee
        row["total"] = tot
        row["nav_change"] = rec.nav - prev_nav
        row["recon_error"] = tot - (rec.nav - prev_nav)
        row["mv"] = {s: q[s] * last.get(s, 0.0) for s in U}
        prev_nav = rec.nav
        out.append(row)
    return pd.DataFrame(out)


def run_b5(world: World, score_start: str, nav: pd.DataFrame, reb: pd.DataFrame, m=12, t=40) -> dict:
    U = world.universe
    mf, capdef = world.frames(m, t)
    wf = world.weekly_frame(m, t)
    ep = max_drawdown_episode(nav, PRIMARY.initial_nav)
    dc = daily_contributions(world, nav, reb, capdef, PRIMARY.initial_nav)
    # window peak -> recovery (or end); peak 'INITIAL' means before the first scored day
    first = 0 if ep["peak_idx"] == 0 else ep["peak_idx"] - 1       # nav row index of peak (path idx - 1)
    last_idx = (ep["recovery_idx"] - 1) if ep["recovery_idx"] is not None else len(nav) - 1
    peak_nav = ep["peak_nav"]
    lin = {l["signal_session"]: l for l in mf.lineage}
    signal_rows = {r["signal_session"]: r for r in mf.rows}
    exec_sorted = sorted((r["execution_session"], r["signal_session"]) for r in capdef.rows if r["status"] == "COMPLETE")
    exec_dates = [e for e, _ in exec_sorted]
    dts = nav["date"].tolist()
    src_week = {e.signal_session: e.source_signal_session for e in world.monthly}
    peak_row = first if ep["peak_idx"] > 0 else -1             # nav row index of the peak day (-1 = initial capital)
    days = list(range(peak_row + 1, last_idx + 1))             # P&L days strictly after the peak close
    months = sorted({dts[i][:7] for i in days})
    navs = nav["nav"].to_numpy(float)
    rows = []
    for mo in months:
        idxs = [i for i in days if dts[i][:7] == mo]
        sub = dc.iloc[idxs]
        j = bisect.bisect_right(exec_dates, dts[idxs[-1]]) - 1
        ex, sig = exec_sorted[j]
        cr, lg = next(r for r in capdef.rows if r["signal_session"] == sig), lin.get(sig, {})
        syms = lg.get("symbols", {})
        held = [s for s in U if cr["weights"].get(s, 0.0) > 1e-10]
        trend = {}
        for s in held:
            sl = syms.get(s, {})
            tp = cr["trend_passes"][s]
            cw, sm = sl.get("current_weekly_close"), sl.get("sma_k")
            trend[s] = f"{'PASS' if tp else 'FAIL'}" + (f" ({cw / sm - 1:+.1%} vs SMA)" if cw and sm else "")
        i_prev = idxs[0] - 1
        start_nav = navs[i_prev] if i_prev >= 0 else PRIMARY.initial_nav
        mv0 = dc.iloc[i_prev]["mv"] if i_prev >= 0 else {s: 0.0 for s in U}
        w0 = ", ".join(f"{s}:{mv0[s] / start_nav:.2f}" for s in U if mv0[s] > 1e-6) or "none"
        rec = {"month": mo, "contains_trough": bool(ep["trough_idx"] - 1 in idxs), "in_peak_to_trough": bool(idxs[-1] <= ep["trough_idx"] - 1),
               "holdings_entering_month": w0, "signal_session": sig, "feature_week_session": src_week.get(sig, ""),
               "execution_session": ex,
               "target_weights": ", ".join(f"{s}:{cr['weights'][s]:.3f}" for s in held) +
                                  (f", CASH:{cr['weights'].get('CASH', 0):.3f}" if cr["weights"].get("CASH", 0) > 1e-10 else ""),
               "def_bond_weight": cr["def_bond_weight"], "trend_filter_at_signal": "; ".join(f"{s} {v}" for s, v in trend.items()),
               "nav_start": float(start_nav), "nav_end": float(navs[idxs[-1]]),
               "month_return": float(navs[idxs[-1]] / start_nav - 1)}
        for s in U:
            rec[f"contrib_{s}_pct_of_peak"] = float(sub[s].sum() / peak_nav * 100)
        rec["contrib_cash_interest_pct_of_peak"] = float(sub["cash_interest"].sum() / peak_nav * 100)
        rec["contrib_fees_pct_of_peak"] = float(sub["fee"].sum() / peak_nav * 100)
        rows.append(rec)
    mdf = pd.DataFrame(rows)
    # peak -> trough aggregate contribution by holding
    p0 = first if ep["peak_idx"] > 0 else 0
    win = dc.iloc[(p0 + 1 if ep["peak_idx"] > 0 else 0):ep["trough_idx"]]   # days after the peak close up to trough close
    agg = {s: float(win[s].sum() / peak_nav * 100) for s in U}
    agg["cash_interest"] = float(win["cash_interest"].sum() / peak_nav * 100)
    agg["fees"] = float(win["fee"].sum() / peak_nav * 100)
    agg["total"] = float(win["total"].sum() / peak_nav * 100)
    agg["nav_change_check"] = float(win["nav_change"].sum() / peak_nav * 100)
    # weekly trend filter status of the names held at the peak, from the peak to the trough
    wrows = [r for r in wf.rows if (ep["peak_date"] if ep["peak_idx"] > 0 else "0") <= r["signal_session"] <= ep["trough_date"]]
    held_at_peak = None
    if ep["peak_idx"] > 0:
        j = bisect.bisect_right(exec_dates, ep["peak_date"]) - 1
        held_at_peak = [s for s in U if capdef.rows[[r["signal_session"] for r in capdef.rows].index(exec_sorted[j][1])]["weights"].get(s, 0) > 1e-10]
    first_fail = {}
    for s in (held_at_peak or []):
        ff = next((r["signal_session"] for r in wrows if not r["trend_passes"].get(s, False)), None)
        nxt_month = None
        if ff:
            nxt_month = next((r["signal_session"] for r in capdef.rows if r["signal_session"] >= ff), None)
            nxt_exec = next((r["execution_session"] for r in capdef.rows if r["signal_session"] >= ff), None)
        else:
            nxt_exec = None
        first_fail[s] = {"first_weekly_trend_fail_after_peak": ff, "next_monthly_signal": nxt_month,
                         "next_monthly_execution": nxt_exec}
    return {"episode": ep, "months": mdf, "peak_to_trough_contrib": agg, "held_at_peak": held_at_peak,
            "first_weekly_fail": first_fail, "recon_max_abs_error": float(dc["recon_error"].abs().max()),
            "daily": dc.drop(columns=["mv"])}


# ------------------------------------------------------------------ orchestration
def pct(x, d=2):
    return "n/a" if x is None or x != x else f"{x * 100:.{d}f}%"


def md_table(df: pd.DataFrame, cols, fmts=None) -> list[str]:
    fmts = fmts or {}
    out = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for _, r in df.iterrows():
        out.append("| " + " | ".join((fmts.get(c, str)(r[c]) if c in r and r[c] == r[c] else "") for c in cols) + " |")
    return out


def run_part_b(world: World, proto: dict, out_dir: Path, tag: str, log=print) -> dict:
    """tag: 'baseline' (Sina self-adjusted data) or 'corrected' (data-corrected dataset)."""
    out_dir = Path(out_dir)
    score_start = world.first_warm_start()
    res0, raw0 = run_b0(world, score_start)
    bench_base = {"CN_60_40": res0["CN_60_40"], "CSI300_BH": res0["CSI300_BH"]}
    log(f"[{tag}] score_start={score_start}  baseline CAP_DEF_M FULL xSharpe={res0['CAP_DEF_M']['FULL']['excess_sharpe']:.4f} "
        f"MDD={res0['CAP_DEF_M']['FULL']['max_drawdown']:.4f}")
    b1 = run_b1(world, score_start, bench_base, proto)
    b1.to_csv(out_dir / f"b1_grid_{tag}.csv", index=False)
    log(f"[{tag}] B1 done: {int(b1['chk_pass'].sum())}/9")
    b2 = run_b2(world, score_start, bench_base, proto)
    b2.to_csv(out_dir / f"b2_lag_{tag}.csv", index=False)
    log(f"[{tag}] B2 done: {b2['chk_pass'].tolist()}")
    b3, b3_notes = run_b3(world, score_start, bench_base, proto)
    b3.to_csv(out_dir / f"b3_substitution_{tag}.csv", index=False)
    log(f"[{tag}] B3 done")
    b4 = run_b4(world, score_start, res0, bench_base, proto)
    b4.to_csv(out_dir / f"b4_small_account_{tag}.csv", index=False)
    log(f"[{tag}] B4 done")
    nav, reb = raw0["CAP_DEF_M"]
    b5 = run_b5(world, score_start, nav, reb)
    b5["months"].to_csv(out_dir / f"b5_monthly_{tag}.csv", index=False)
    b5["daily"].to_csv(out_dir / f"b5_daily_contributions_{tag}.csv", index=False)
    log(f"[{tag}] B5 done: {b5['episode']['peak_date']} -> {b5['episode']['trough_date']} -> {b5['episode']['recovery_date']}")
    n_pass1 = int(b1["chk_pass"].sum())
    verdict = {
        "B1": {"cells_passed": n_pass1, "of": 9, "holds": n_pass1 >= 7},
        "B2": {"passed": b2["chk_pass"].tolist(), "holds": bool(b2["chk_pass"].all())},
        "B3": {"runs": b3.to_dict("records"), "holds": None},
        "B4": {"holds": bool(b4[b4["capital"] == 100000]["holds_condition"].dropna().iloc[0])},
        "B5": {"descriptive": True},
    }
    ran = b3[b3["run"] == "RUN"]
    if len(ran) == 0:
        verdict["B3"]["holds"] = "NOT RUN"
    else:
        verdict["B3"]["holds"] = bool(ran["chk_pass"].all()) and (len(ran) == len(b3))
        if len(ran) < len(b3):
            verdict["B3"]["note"] = "some variants NOT RUN; holds_if applies to every run that can be run"
            verdict["B3"]["holds"] = bool(ran["chk_pass"].all())
    return {"tag": tag, "score_start": score_start, "baseline": res0, "bench": bench_base, "b1": b1, "b2": b2, "b3": b3,
            "b3_notes": b3_notes, "b4": b4, "b5": b5, "verdict": verdict}
