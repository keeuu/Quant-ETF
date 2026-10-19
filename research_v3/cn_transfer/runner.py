"""Orchestrates the pre-registered CN transfer test: data checks, signals, 4 strategies x 2 cost settings,
partition metrics, bootstrap, outcome classification and artifact writing."""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from research_v2.contracts import SignalFrame
from research_v2.metrics import compute_backtest_metrics
from research_v2.signals import compute_matched_monthly_signals
from research_v3.config import V3Config
from research_v3.signals_step3 import make_defensive_provider
from research_v3.stats import bootstrap_sharpe_diff, psr, stationary_bootstrap_indices
from research_v3.step3 import MIN_YEAR_SESSIONS, calendar_year_returns

from .calendar_cn import (CN_TZ, build_monthly_matched_schedule_cal, build_weekly_schedule_cal, get_xshg_calendar,
                          market_as_of_after_close_cal)
from .data import DATA_DIR, load_bars, load_shibor, validate_bars, verify_manifest
from .engine_cn import run_backtest_cn
from .protocol import load_amendment_verified, load_verified
from .signals_cn import (ShiborCurve, cap_def_frame, compute_model_signals_cn, time_weighted_weight)

HERE = Path(__file__).resolve().parent
ART_DIR = HERE / "artifacts"
SCORE_END_CAP = "2026-09-30"           # protocol: "not later than 2026-09-30"
SUB_SPLIT = "2020-12-31"               # protocol: SUB_A ends 2020-12-31, SUB_B starts 2021-01-01
COST_SETTINGS = {"primary": V3Config(cost_bps=5.0, slippage_bps=5.0, cash_yield_source="irx"),
                 "secondary_10_10": V3Config(cost_bps=10.0, slippage_bps=10.0, cash_yield_source="irx")}
STRATEGIES = ["CAP_DEF_M", "CSI300_BH", "CN_60_40", "BASE_M_CN"]
PARTITIONS = ["FULL", "SUB_A", "SUB_B"]


class CNRunAbort(RuntimeError):
    """Raised when a pre-registered fail-closed rule stops the run (report, do not patch)."""


def protocol_groups(proto: dict) -> dict[str, list[str]]:
    return {k.split(" (")[0]: v for k, v in proto["mapping_from_us_to_cn"]["groups"].items()}


def month_end_sessions(cal, start: str, end: str) -> list[str]:
    s = [x.strftime("%Y-%m-%d") for x in cal.sessions_in_range(start, end)]
    out = []
    for a, b in zip(s, s[1:] + [None]):
        if b is None or b[:7] != a[:7]:
            out.append(a)
    return out


def choose_score_end(cal, data_last: str, cap: str = SCORE_END_CAP) -> str:
    """Last session of the last calendar month that is complete in the data (<= cap)."""
    lim = min(data_last, cap)
    cands = []
    for me in month_end_sessions(cal, "2013-01-01", lim):
        nxt = cal.next_session(pd.Timestamp(me)).strftime("%Y-%m-%d")
        if nxt[:7] != me[:7]:   # a true month-end session (next session lies in a later month)
            cands.append(me)
    return cands[-1]


def data_checks(bars, cal, data_start: str, score_end: str) -> dict:
    sessions = [x.strftime("%Y-%m-%d") for x in cal.sessions_in_range(data_start, score_end)]
    rep = validate_bars({s: b for s, b in bars.items()}, sessions)
    rep["missing_in_data_range"] = {}
    for sym, df in bars.items():
        have = set(df["Date"])
        miss = [s for s in sessions if s not in have]
        rep["missing_in_data_range"][sym] = miss
        ratio = (df["adj_close"] / df["close"]).to_numpy()
        jumps = int((np.abs(ratio[1:] / ratio[:-1] - 1.0) > 1e-3).sum())
        rep["per_symbol"][sym].update(
            adj_to_raw_ratio_first=float(ratio[0]), adj_to_raw_ratio_last=float(ratio[-1]), adj_ratio_jumps=jumps,
            raw_close_first=float(df["close"].iloc[0]), raw_close_last=float(df["close"].iloc[-1]),
            adj_close_first=float(df["adj_close"].iloc[0]), adj_close_last=float(df["adj_close"].iloc[-1]))
    rep["n_sessions"] = len(sessions)
    return rep


def resolve_missing(bars, missing: dict, ver: pd.DataFrame | None):
    """Amendment 1, rule 1: for each missing (symbol, session) look for an independent unadjusted open/close
    (Tencent window in the snapshot). Insert (adjusted with the previous bar's factor, valid on non-event days) only if
    found AND consistent (< 10% from the adjusted closes of the neighbouring bars); otherwise it is a suspension."""
    out = {k: v.copy() for k, v in bars.items()}
    res = []
    for sym, dates in missing.items():
        for d in dates:
            rec = {"symbol": sym, "session": d, "independent_source": "Tencent ak.stock_zh_a_hist_tx (unadjusted); "
                   "Eastmoney unreachable", "resolution": "SUSPENSION", "detail": "no bar in independent source"}
            cand = None
            if ver is not None:
                x = ver[(ver.symbol.astype(str) == sym) & (ver.date == d)]
                if len(x) and x.open.iloc[0] > 0 and x.close.iloc[0] > 0:
                    cand = x.iloc[0]
            if cand is not None:
                df = out[sym]
                prev, nxt = df[df.Date < d].iloc[-1], df[df.Date > d]
                fac = prev.adj_close / prev.close
                adj_c = cand.close * fac
                ok = abs(adj_c / prev.adj_close - 1) < 0.10 and (len(nxt) == 0 or abs(nxt.iloc[0].adj_close / adj_c - 1) < 0.10)
                if ok:
                    row = {"Date": d, "open": cand.open, "high": cand.high, "low": cand.low, "close": cand.close,
                           "adj_close": adj_c, "adj_open": cand.open * fac, "volume": cand.volume}
                    out[sym] = pd.concat([df, pd.DataFrame([row])]).sort_values("Date").reset_index(drop=True)
                    rec.update(resolution="VERIFIED_INSERTED", detail="independent open/close consistent with neighbours")
                else:
                    rec["detail"] = "independent bar inconsistent with neighbouring closes"
            res.append(rec)
    return out, res


def shibor_tail_report(sh: pd.Series, score_end: str) -> dict:
    v = sh.values
    n = 1
    while n < len(v) and v[-1 - n] == v[-1]:
        n += 1
    idx = list(sh.index)
    flat = idx[-n:]
    return {"flat_value": float(v[-1]), "flat_rows": n, "flat_first": flat[0], "flat_last": flat[-1],
            "flat_rows_on_or_before_score_end": int(sum(d <= score_end for d in flat)),
            "prior_90_rows_range": [float(v[-90 - n:-n].min()), float(v[-90 - n:-n].max())]}


def build_frames(proto, bars, universe, groups, sessions, weekly, monthly, curve, def_symbol):
    wf = compute_model_signals_cn(bars, universe, sessions, weekly, "W_M12_T40", momentum_months=12, trend_weeks=40)
    mf = compute_matched_monthly_signals(wf, monthly, "M_M12_T40")
    return wf, mf


def first_fully_warm_row(mf: SignalFrame, universe) -> int | None:
    for i, (r, lin) in enumerate(zip(mf.rows, mf.lineage)):
        if r["status"] != "COMPLETE":
            continue
        ok = all(r["momentum_scores"].get(s) is not None and math.isfinite(r["momentum_scores"][s])
                 and lin["symbols"][s]["sma_k"] is not None for s in universe)
        if ok:
            return i
    return None


def _mk(model_id, rows, wfun, universe):
    out = []
    for r in rows:
        w = {s: 0.0 for s in universe}
        w["CASH"] = 0.0
        w.update(wfun(r))
        out.append({"signal_session": r["signal_session"], "execution_session": r["execution_session"],
                    "status": "COMPLETE", "weights": w, "def_bond_weight": 0.0})
    return SignalFrame(model_id=model_id, rows=out, lineage=[])


def strategy_frames(mf, capdef, score_start, score_end, universe):
    sched = [r for r in mf.rows if r["status"] == "COMPLETE" and score_start <= r["execution_session"] <= score_end]
    return {
        "CAP_DEF_M": capdef,
        "BASE_M_CN": mf,
        "CSI300_BH": _mk("CSI300_BH", sched[:1], lambda r: {"510300": 1.0}, universe),
        "CN_60_40": _mk("CN_60_40", sched, lambda r: {"510300": 0.6, "511010": 0.4}, universe),
    }


def daily_rf(dates, curve) -> np.ndarray:
    return np.array([0.0] + [curve.accrual_factor(dates[i - 1], dates[i]) - 1.0 for i in range(1, len(dates))])


def partition_bounds(score_start, score_end):
    return {"FULL": (score_start, score_end), "SUB_A": (score_start, SUB_SPLIT), "SUB_B": ("2021-01-01", score_end)}


def metrics_for(cal, nav, reb, frame, rf, part, start, end, initial_nav, year_tbl):
    m = nav[(nav["date"] >= start) & (nav["date"] <= end)]
    idx0 = m.index[0]
    prev_nav = initial_nav if idx0 == 0 else float(nav["nav"].iloc[idx0 - 1])
    rb = reb[(reb["execution_session"] >= start) & (reb["execution_session"] <= end)] if len(reb) else reb
    met = compute_backtest_metrics(
        daily_nav=m.reset_index(drop=True), rebalances=rb.reset_index(drop=True), initial_nav=prev_nav,
        first_session_open_utc=cal.session_open(pd.Timestamp(m["date"].iloc[0])),
        last_session_close_utc=cal.session_close(pd.Timestamp(m["date"].iloc[-1])))
    r = m["daily_return"].to_numpy(float)
    ex = r - rf[m.index.to_numpy()]
    sd = ex.std(ddof=1)
    xs = float(ex.mean() / sd * math.sqrt(252.0)) if sd > 1e-12 else float("nan")
    y0, y1 = int(start[:4]), int(end[:4])
    yy = year_tbl[(year_tbl.index >= y0) & (year_tbl.index <= y1) & (year_tbl["sessions"] >= MIN_YEAR_SESSIONS)]["ret"]
    if part == "SUB_B":   # SUB_B starts 01-01 so full calendar years are the same as in the full table
        pass
    dates = m["date"].tolist()
    return ex, {
        "partition": part, "start": dates[0], "end": dates[-1], "n_sessions": len(m),
        "cagr": met["cagr"], "ann_vol": met["annualized_volatility"],
        "sharpe": met["sharpe_ratio"] if met["sharpe_ratio"] is not None else float("nan"),
        "excess_sharpe": xs, "max_drawdown": met["max_drawdown"],
        "worst_year": float(yy.min()) if len(yy) else float("nan"),
        "ann_one_way_turnover": met["annualized_one_way_turnover"], "mean_cash_weight": met["mean_cash_weight"],
        "mean_bond_def_weight": time_weighted_weight(dates, frame, key="def_bond_weight"),
        "mean_bond_total_target_weight": time_weighted_weight(dates, frame, symbol="511010"),
        "psr0_excess": psr(ex, 0.0), "final_nav": met["final_nav"], "total_fees": met["total_fees"],
        "rebalance_count": met["rebalance_count"], "elapsed_years": met["elapsed_years"],
    }


def classify(tbl: pd.DataFrame) -> dict:
    """Pre-registered outcome from the primary-cost point estimates."""
    g = lambda part, strat, col: float(tbl[(tbl.partition == part) & (tbl.strategy == strat)][col].iloc[0])
    c1 = g("FULL", "CAP_DEF_M", "excess_sharpe") > g("FULL", "CN_60_40", "excess_sharpe")
    c2 = g("FULL", "CAP_DEF_M", "max_drawdown") < g("FULL", "CSI300_BH", "max_drawdown")
    c3a = g("SUB_A", "CAP_DEF_M", "excess_sharpe") > g("SUB_A", "CN_60_40", "excess_sharpe")
    c3b = g("SUB_B", "CAP_DEF_M", "excess_sharpe") > g("SUB_B", "CN_60_40", "excess_sharpe")
    sharpe_all = c1 and c3a and c3b
    if c1 and c2 and c3a and c3b:
        out = "SUPPORTED"
    elif (not c2) and (not c1):
        out = "NOT_SUPPORTED"
    else:
        out = "PARTIAL"
    return {"outcome": out, "C1_full_xsharpe_gt_60_40": bool(c1), "C2_full_mdd_lt_csi300_bh": bool(c2),
            "C3a_sub_a_xsharpe_gt_60_40": bool(c3a), "C3b_sub_b_xsharpe_gt_60_40": bool(c3b),
            "all_sharpe_criteria_met": bool(sharpe_all)}


def run(out_dir: Path = ART_DIR, data_dir: Path = DATA_DIR, log=print) -> dict:
    proto = load_verified()                   # raises ProtocolChangedError on hash mismatch
    amend = load_amendment_verified()         # amendment 1 (suspended sessions), hash-verified
    assert amend["amendment"] == 1
    manifest = verify_manifest(data_dir)      # raises CNDataError on hash mismatch
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    mp = proto["mapping_from_us_to_cn"]
    universe = tuple(mp["universe_order"])
    groups = protocol_groups(proto)
    def_symbol = mp["DEFENSIVE_BOND"].split()[0]
    assert def_symbol == "511010"
    rule = proto["rule_unchanged"]
    top_n = rule["top_n"]
    boot = [m for m in proto["metrics"] if m.startswith("stationary block bootstrap")][0]
    cal = get_xshg_calendar()

    raw_bars = load_bars(universe, data_dir)
    data_start = max(df["Date"].iloc[0] for df in raw_bars.values())
    data_last = min(df["Date"].iloc[-1] for df in raw_bars.values())
    score_end = choose_score_end(cal, data_last)
    bars = {s: df[(df["Date"] >= data_start) & (df["Date"] <= score_end)].reset_index(drop=True)
            for s, df in raw_bars.items()}
    rep = data_checks(bars, cal, data_start, score_end)
    if rep["errors"]:
        raise CNRunAbort(f"data validation errors: {rep['errors']}")
    sessions = [x.strftime("%Y-%m-%d") for x in cal.sessions_in_range(data_start, score_end)]
    shibor = load_shibor(data_dir)
    curve = ShiborCurve(shibor)
    vp = Path(data_dir) / "tencent_verification_raw.csv"
    ver = pd.read_csv(vp, dtype={"symbol": str}) if vp.is_file() else None
    raw_bars_for_original = bars
    bars, resolution = resolve_missing(bars, rep["missing_in_data_range"], ver)
    rep["missing_bar_resolution"] = resolution
    rep["shibor_tail"] = shibor_tail_report(shibor, score_end)

    as_of = market_as_of_after_close_cal(cal, score_end, CN_TZ)
    weekly = build_weekly_schedule_cal(cal, sessions, as_of)
    monthly = build_monthly_matched_schedule_cal(cal, sessions, as_of, weekly)
    wf, mf = build_frames(proto, bars, universe, groups, sessions, weekly, monthly, curve, def_symbol)
    i0 = first_fully_warm_row(mf, universe)
    if i0 is None:
        raise CNRunAbort("no monthly signal with all assets warmed up")
    score_start = mf.rows[i0]["execution_session"]
    defensive = make_defensive_provider(bars, sessions, curve, symbol=def_symbol, months=3)
    capdef = cap_def_frame(mf, "CAP_DEF_M", universe=universe, cap_groups=groups, defensive=defensive,
                           def_symbol=def_symbol, top_n=top_n)
    frames = strategy_frames(mf, capdef, score_start, score_end, universe)
    bounds = partition_bounds(score_start, score_end)

    rep["score_start"], rep["score_end"], rep["data_start"] = score_start, score_end, data_start
    rep["first_warm_signal_session"] = mf.rows[i0]["signal_session"]
    rep["missing_in_scoring_sample"] = {s: [d for d in v if score_start <= d <= score_end]
                                        for s, v in rep["missing_in_data_range"].items()}
    log(f"data_start={data_start} score_start={score_start} score_end={score_end}")
    log("missing bars (data range): " + json.dumps({s: len(v) for s, v in rep['missing_in_data_range'].items()}))
    log("missing bars (scoring sample): " + json.dumps({s: len(v) for s, v in rep['missing_in_scoring_sample'].items()}))

    # status under the ORIGINAL protocol (fail-closed, primary cost, raw bars)
    original_status = {}
    for strat in STRATEGIES:
        try:
            run_backtest_cn(cal, raw_bars_for_original, score_start, score_end, frames[strat], COST_SETTINGS["primary"],
                            curve, universe)
            original_status[strat] = "RUN"
        except Exception as e:
            original_status[strat] = f"NOT RUN: {type(e).__name__}: {e}"
    rep["original_protocol_status"] = original_status
    log("original-protocol status: " + json.dumps(original_status))

    rows, cy_rows, navs, rebs, post = [], [], {}, {}, []
    series = {}   # (cost, part, strat) -> excess return array
    for cname, cfg in COST_SETTINGS.items():
        for strat in STRATEGIES:
            try:
                nav, reb, m = run_backtest_cn(cal, bars, score_start, score_end, frames[strat], cfg, curve, universe,
                                              suspension_rules=True)
            except Exception as e:  # fail-closed engine/ledger errors: report, do not patch
                raise CNRunAbort(f"fail-closed stop in {strat}/{cname}: {type(e).__name__}: {e}") from e
            navs[(cname, strat)], rebs[(cname, strat)] = nav, reb
            for pr in m["postponements"]:
                post.append({"cost_setting": cname, "strategy": strat, **pr})
            if m["carried_valuation_days"]:
                rep.setdefault("carried_valuation_days", {})[f"{cname}/{strat}"] = m["carried_valuation_days"]
            rf = daily_rf(nav["date"].tolist(), curve)
            nav["rf_accrual"] = rf
            yt = calendar_year_returns(nav, cfg.initial_nav)
            for y, r in yt.iterrows():
                cy_rows.append({"cost_setting": cname, "strategy": strat, "year": int(y), "return": r["ret"],
                                "sessions": int(r["sessions"]), "full_year": bool(r["sessions"] >= MIN_YEAR_SESSIONS)})
            for part, (a, b) in bounds.items():
                ex, row = metrics_for(cal, nav, reb, frames[strat], rf, part, a, b, cfg.initial_nav, yt)
                series[(cname, part, strat)] = ex
                rows.append({"cost_setting": cname, "strategy": strat, **row})
    tbl = pd.DataFrame(rows)
    cy = pd.DataFrame(cy_rows)

    # bootstrap (primary cost): excess-Sharpe difference of CAP_DEF_M vs benchmarks
    seed, nres, blk = 20261019, 2000, 21
    assert "block 21" in boot and "2000" in boot and "20261019" in boot
    brows = []
    for part in PARTITIONS:
        a = series[("primary", part, "CAP_DEF_M")]
        idx = stationary_bootstrap_indices(len(a), blk, nres, seed)
        for ref in ("CN_60_40", "CSI300_BH"):
            pt, lo, hi = bootstrap_sharpe_diff(a, series[("primary", part, ref)], idx, 0.90)
            brows.append({"partition": part, "strategy": "CAP_DEF_M", "reference": ref,
                          "xsharpe_diff": pt, "ci90_lo": lo, "ci90_hi": hi, "ci_includes_0": bool(lo <= 0 <= hi),
                          "block": blk, "n_resamples": nres, "seed": seed})
    bt = pd.DataFrame(brows)

    prim = tbl[tbl.cost_setting == "primary"]
    verdict = classify(prim)

    # ---- write artifacts
    pd.DataFrame(post, columns=["cost_setting", "strategy", "signal_session", "scheduled_execution",
                                "suspended_symbols", "actual_execution"]).to_csv(out_dir / "postponed_rebalances.csv", index=False)
    rep["n_postponements"] = len(post)
    tbl.to_csv(out_dir / "metrics_long.csv", index=False)
    cy.to_csv(out_dir / "calendar_year_returns.csv", index=False)
    bt.to_csv(out_dir / "bootstrap.csv", index=False)
    for (cname, strat), nav in navs.items():
        nav.to_csv(out_dir / f"nav_{cname}_{strat}.csv", index=False)
    pd.DataFrame([{"signal_session": r["signal_session"], "execution_session": r["execution_session"],
                   "status": r["status"], "selected": ",".join(r["selected_symbols"]),
                   "def_bond_weight": r["def_bond_weight"], **{f"w_{k}": v for k, v in r["weights"].items()}}
                  for r in capdef.rows]).to_csv(out_dir / "signals_CAP_DEF_M.csv", index=False)
    rep["run_manifest"] = {"akshare_version": manifest["akshare_version"], "download_date": manifest["download_date"],
                           "protocol_sha256": (HERE / "protocol_cn_transfer.sha256").read_text().strip(),
                           "n_weekly_events": len(weekly), "n_monthly_events": len(monthly)}
    (out_dir / "data_report.json").write_text(json.dumps(rep, indent=2, default=str))
    (out_dir / "verdict.json").write_text(json.dumps(verdict, indent=2))
    write_summary(out_dir, tbl, cy, bt, verdict, rep, manifest, pd.DataFrame(post))
    return {"verdict": verdict, "tbl": tbl, "cy": cy, "boot": bt, "report": rep}


def _p(x):
    return f"{x * 100:.2f}%" if x == x else "n/a"


def write_summary(out_dir, tbl, cy, bt, verdict, rep, manifest, post):
    prim = tbl[tbl.cost_setting == "primary"]
    L = ["# CN transfer test of frozen CAP_DEF_M (protocol_cn_transfer.json)", "",
         f"Outcome (primary cost 5+5 bps, point estimates): **{verdict['outcome']}**", "",
         "## Status under the ORIGINAL protocol (fail-closed on missing bars)", ""] + \
        [f"- {k}: {v}" for k, v in rep["original_protocol_status"].items()] + [
         "", "The strategy runs that hold the ETFs with missing bars are NOT RUN under the original protocol. "
         "All results below are from protocol_cn_transfer_amendment1.json (suspension rules), applied identically to "
         "all four strategies and both cost settings.", "",
         "## Criteria (amended run)", "",
         "| criterion | met |", "|---|---|",
         f"| FULL excess Sharpe CAP_DEF_M > CN_60_40 | {verdict['C1_full_xsharpe_gt_60_40']} |",
         f"| FULL max drawdown CAP_DEF_M < CSI300_BH | {verdict['C2_full_mdd_lt_csi300_bh']} |",
         f"| SUB_A excess Sharpe CAP_DEF_M > CN_60_40 | {verdict['C3a_sub_a_xsharpe_gt_60_40']} |",
         f"| SUB_B excess Sharpe CAP_DEF_M > CN_60_40 | {verdict['C3b_sub_b_xsharpe_gt_60_40']} |", "",
         "Outcome rule: SUPPORTED = all met; NOT_SUPPORTED = neither the drawdown criterion nor the FULL Sharpe "
         "criterion met; otherwise PARTIAL.", "",
         f"Sample: data_start {rep['data_start']}, score_start {rep['score_start']} (first monthly execution with all 6 "
         f"assets warmed up), score_end {rep['score_end']}. SHIBOR cash, akshare {manifest['akshare_version']}, "
         f"downloaded {manifest['download_date']}.", ""]
    for cname, title in (("primary", "Primary (5+5 bps)"), ("secondary_10_10", "Secondary sensitivity (10+10 bps), not used for decisions")):
        L += [f"## Metrics: {title}", ""]
        for part in PARTITIONS:
            sub = tbl[(tbl.cost_setting == cname) & (tbl.partition == part)].set_index("strategy").loc[STRATEGIES]
            L += [f"### {part} ({sub['start'].iloc[0]} to {sub['end'].iloc[0]})", "",
                  "| strategy | CAGR | vol | Sharpe | xSharpe | MDD | worst yr | turnover/yr | cash wt | bond-def wt | PSR(0) |",
                  "|---|---|---|---|---|---|---|---|---|---|---|"]
            for s, r in sub.iterrows():
                L.append(f"| {s} | {_p(r.cagr)} | {_p(r.ann_vol)} | {r.sharpe:.3f} | {r.excess_sharpe:.3f} | {_p(r.max_drawdown)} | "
                         f"{_p(r.worst_year)} | {r.ann_one_way_turnover:.2f} | {_p(r.mean_cash_weight)} | "
                         f"{_p(r.mean_bond_def_weight)} | {r.psr0_excess:.3f} |")
            L.append("")
    L += ["## Calendar-year returns (primary cost; * = partial year)", ""]
    cp = cy[cy.cost_setting == "primary"].pivot(index="year", columns="strategy", values="return")[STRATEGIES]
    fy = cy[(cy.cost_setting == "primary") & (cy.strategy == "CAP_DEF_M")].set_index("year")["full_year"]
    L += ["| year | " + " | ".join(STRATEGIES) + " |", "|---|" + "---|" * len(STRATEGIES)]
    for y, r in cp.iterrows():
        L.append(f"| {y}{'' if fy[y] else '*'} | " + " | ".join(_p(r[s]) for s in STRATEGIES) + " |")
    L += ["", "## Bootstrap: 90% CI of excess-Sharpe difference (CAP_DEF_M minus reference), primary cost", "",
          "Stationary block bootstrap, mean block 21, 2000 resamples, seed 20261019.", "",
          "| partition | reference | diff | CI lo | CI hi | CI includes 0 |", "|---|---|---|---|---|---|"]
    for r in bt.itertuples():
        L.append(f"| {r.partition} | {r.reference} | {r.xsharpe_diff:+.3f} | {r.ci90_lo:+.3f} | {r.ci90_hi:+.3f} | {r.ci_includes_0} |")
    L += [""]
    inc = bt[bt.ci_includes_0]
    if len(inc):
        L.append("Where the CI includes 0 the difference is **not statistically distinguishable** from zero: " +
                 "; ".join(f"{r.partition} vs {r.reference}" for r in inc.itertuples()) + ".")
    exc = bt[~bt.ci_includes_0]
    if len(exc):
        L.append("CI excludes 0: " + "; ".join(f"{r.partition} vs {r.reference}" for r in exc.itertuples()) + ".")
    L += ["", "## Deviations and data notes", "",
          "- **Data source (implementation note, not a protocol change):** Eastmoney `fund_etf_hist_em` (hfq) refused "
          "connections; bars are Sina unadjusted OHLC (`ak.fund_etf_hist_sina`).",
          "- **Self-built adjustment:** total-return factor reinvesting each cash distribution at the ex-date close "
          "(Sina `hfq.js` cumulative dividend `u`) and applying share-conversion multipliers (`s`); same-day factor on "
          "open and close.",
          "- **Conversion alignment:** Sina dates 510500's 2022 conversion at the record date 2022-08-26; the multiplier is "
          "applied on the bar (within 3) whose close ratio best matches (2022-08-29). The 2015-04-15 conversion aligns on its date.",
          "- **Sharpe annualisation sqrt(252)** (v2 convention); SUB_A/SUB_B are slices of one continuous FULL run.",
          "- **Missing bars and their resolution (amendment 1, rule 1):**"]
    for r in rep["missing_bar_resolution"]:
        L.append(f"  - {r['symbol']} {r['session']}: {r['resolution']} ({r['detail']}; source tried: {r['independent_source']})")
    L.append(f"- **Postponed rebalances (rule 3), all strategies and cost settings:** {len(post)}")
    for r in post.itertuples():
        L.append(f"  - {r.cost_setting}/{r.strategy}: signal {r.signal_session}, scheduled {r.scheduled_execution}, "
                 f"suspended {r.suspended_symbols}, executed {r.actual_execution}")
    L.append(f"- **Carry-forward valuation days (suspended while held):** {json.dumps(rep.get('carried_valuation_days', {}))}")
    st = rep["shibor_tail"]
    L += [f"- **SHIBOR tail:** the last {st['flat_rows']} rows ({st['flat_first']} to {st['flat_last']}) are flat at "
          f"{st['flat_value']}; {st['flat_rows_on_or_before_score_end']} of them are on or before score_end. Plausible: "
          f"the preceding 90 rows ranged {st['prior_90_rows_range'][0]:.3f} to {st['prior_90_rows_range'][1]:.3f} "
          "(3M SHIBOR has sat near 1.43 for months and the flat run is the same level at 2-decimal precision), and "
          "the 1M/6M tenors in the cross-check source still move. Data not altered."]
    L += ["", "## Data", "", f"Missing bars inside the scoring sample: {json.dumps({s: len(v) for s, v in rep['missing_in_scoring_sample'].items()})}",
          f"Missing bars in the whole data range (warm-up included): {json.dumps({s: len(v) for s, v in rep['missing_in_data_range'].items()})}",
          f"Bars on non-session days: {json.dumps({s: len(v) for s, v in rep['extra_session_bars'].items()})}", ""]
    (out_dir / "summary.md").write_text("\n".join(L) + "\n")
