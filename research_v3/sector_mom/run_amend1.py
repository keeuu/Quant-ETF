"""Amendment 1: S5-S7 (academic momentum) on DEV; selection over S1-S7 (N = 7). Holdout untouched."""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

from research_v3.stats import bootstrap_sharpe_diff, dsr, psr, sample_moments, stationary_bootstrap_indices, expected_max_sr

from . import guard
from .engine import PRIMARY, SENS, ZERO, Plan, StratSpec, simulate
from .metrics import calendar_year_table, daily_rf, slice_metrics
from .protocol_amend import load_amendment_verified
from .report import ORDER as ORDER4, f3, pc, table
from .run_dev import (ART, CAPITAL, COSTS, RUNS, SPECS, STRATS4, MIN_UNIVERSE, build_everything, make_plan, periods)

NEW = [("S5_IND_MOM_12_1", 252, False), ("S6_IND_MOM_6_1", 126, False), ("S7_DUAL_MOM_12_1", 252, True)]
NEW_NAMES = [n for n, _, _ in NEW]
POOL = STRATS4 + NEW_NAMES
MIN_AGE_AMEND = 253


def month_end_events(panel, start_sig_pos: int):
    out = []
    S = panel.sessions
    for i in range(start_sig_pos, len(S) - 1):
        if S[i][:7] != S[i + 1][:7]:
            out.append((i, i + 1))
    return out


def build_amend_plans(panel, curve, start_pos):
    """Returns {name: Plan}, rows for csv, exact first execution."""
    events = month_end_events(panel, start_pos - 1)
    first = None
    plans = {n: Plan() for n in NEW_NAMES}
    rows = []
    for sp, ep in events:
        if ep > len(panel.sessions) - 1:
            break
        elig = panel.eligible_dedup(sp, MIN_AGE_AMEND)
        if first is None:
            if len(elig) < MIN_UNIVERSE:
                continue
            first = ep
        row = {"signal_date": panel.sessions[sp], "execution_date": panel.sessions[ep], "n_eligible": len(elig)}
        for n, lb, dual in NEW:
            tg = panel.amend_target(sp, elig, lb, curve, dual)
            plans[n].weekly.append((sp, ep, tg))      # 'weekly' slot reused as the mom-schedule container
            row[n] = ";".join(f"{k}:{v:.3f}" for k, v in tg.items())
        rows.append(row)
    return plans, pd.DataFrame(rows), first


def select_pool(m: pd.DataFrame) -> dict:
    g = lambda cost, s, per, col: float(m[(m.cost == cost) & (m.strategy == s) & (m.period == per)][col].iloc[0])
    detail = {}
    for s in POOL:
        d = {}
        for cost in ("primary", "sens"):
            x = dict(xs_DEV=g(cost, s, "DEV", "xsharpe"), xs_DEV_EW=g(cost, "EW_SECTOR", "DEV", "xsharpe"),
                     xs_DEV_CSI=g(cost, "CSI300_BH", "DEV", "xsharpe"), mdd_DEV=g(cost, s, "DEV", "max_drawdown"),
                     mdd_DEV_CSI=g(cost, "CSI300_BH", "DEV", "max_drawdown"), xs_A=g(cost, s, "DEV_A", "xsharpe"),
                     xs_A_EW=g(cost, "EW_SECTOR", "DEV_A", "xsharpe"), xs_B=g(cost, s, "DEV_B", "xsharpe"),
                     xs_B_EW=g(cost, "EW_SECTOR", "DEV_B", "xsharpe"))
            x["C1_xs_gt_EW_and_CSI"] = bool(x["xs_DEV"] > x["xs_DEV_EW"] and x["xs_DEV"] > x["xs_DEV_CSI"])
            x["C2_mdd_lt_CSI"] = bool(x["mdd_DEV"] < x["mdd_DEV_CSI"])
            x["C3_xs_gt_EW_in_A_and_B"] = bool(x["xs_A"] > x["xs_A_EW"] and x["xs_B"] > x["xs_B_EW"])
            x["all"] = bool(x["C1_xs_gt_EW_and_CSI"] and x["C2_mdd_lt_CSI"] and x["C3_xs_gt_EW_in_A_and_B"])
            d[cost] = x
        d["qualifies"] = bool(d["primary"]["all"] and d["sens"]["all"])
        d["min_xs_A_B_primary"] = min(d["primary"]["xs_A"], d["primary"]["xs_B"])
        detail[s] = d
    q = [s for s in POOL if detail[s]["qualifies"]]
    return {"candidate": max(q, key=lambda s: detail[s]["min_xs_A_B_primary"]) if q else None, "qualifiers": q, "detail": detail}


def replay_gross(panel, curve, plan, start_pos, end_pos, capital=1e6):
    """Independent zero-cost replay: at each exec open set shares = w * V_open / open (V_open from previous shares at
    that open + cash), cash accrues SHIBOR; value at end close. Blocked orders ignored (reported by the engine)."""
    S = panel.sessions
    cash, shares = capital, {}
    ev = {ep: tg for sp, ep, tg in plan.weekly if ep <= end_pos}
    for i in range(start_pos, end_pos + 1):
        if i > start_pos:
            cash *= curve.accrual_factor(S[i - 1], S[i])
        if i in ev:
            px = lambda t: panel.O[i, panel.col[t]] if not np.isnan(panel.O[i, panel.col[t]]) else panel.CLf[i - 1, panel.col[t]]
            V = cash + sum(q * px(t) for t, q in shares.items())
            shares = {t: w * V / px(t) for t, w in ev[i].items()}
            cash = V - sum(w * V for w in ev[i].values())
    mv = sum(q * (panel.C[end_pos, panel.col[t]] if not np.isnan(panel.C[end_pos, panel.col[t]]) else panel.CLf[end_pos, panel.col[t]])
             for t, q in shares.items())
    return cash + mv


def run(log=print):
    am = load_amendment_verified()
    assert am["amendment"] == 1
    proto, inc, man, missing, panel, curve, cal, weekly = build_everything(log)
    plan, uot, sel, start_pos, end_pos = make_plan(panel, weekly, log)
    plans, mrows_df, first_ep = build_amend_plans(panel, curve, start_pos)
    start, end = panel.sessions[start_pos], panel.sessions[end_pos]
    pers, mid = periods(start, end)
    log(f"common window {start}..{end}; S5-S7 first execution {panel.sessions[first_ep]}")
    guard.assert_date_allowed(panel.sessions[end_pos])
    dates = panel.sessions[start_pos:end_pos + 1]
    rf = daily_rf(dates, curve)
    specs = {s.name: (s, plan) for s in SPECS}
    for n in NEW_NAMES:
        specs[n] = (StratSpec(n, "mom"), plans[n])
    mrows, cyrows, results, ex = [], [], {}, {}
    for cname, lots in RUNS:
        cap = CAPITAL[lots]
        for name, (spec, pl) in specs.items():
            res = simulate(panel, pl, spec, COSTS[cname], cap, lots, curve, start_pos, end_pos)
            results[(cname, lots, name)] = res
            for per, (a, b) in pers.items():
                mt, e = slice_metrics(res, rf, a, b, cap, include_counters=(per == "DEV"))
                ex[(cname, lots, name, per)] = e
                mrows.append({"cost": cname, "lot_mode": "lots100k" if lots else "fractional1m", "strategy": name, "period": per, **mt})
            for y, r in calendar_year_table(res.nav).iterrows():
                cyrows.append({"cost": cname, "lot_mode": "lots100k" if lots else "fractional1m", "strategy": name, "year": y,
                               "return": r["ret"], "full_year": r["full_year"]})
    M = pd.DataFrame(mrows)
    z = M[(M.cost == "zero") & (M.period == "DEV")].set_index("strategy")["cagr"]
    M["cost_drag"] = np.nan
    for cname in ("primary", "sens"):
        for s in z.index:
            m = (M.cost == cname) & (M.lot_mode == "fractional1m") & (M.strategy == s)
            M.loc[m, "cost_drag"] = z[s] - M.loc[m, "cagr"]
    A = Path(ART)
    M.to_csv(A / "metrics_wide_amend1.csv", index=False)
    M.melt(id_vars=["cost", "lot_mode", "strategy", "period"], var_name="metric", value_name="value").to_csv(A / "metrics_long_amend1.csv", index=False)
    pd.DataFrame(cyrows).to_csv(A / "calendar_year_returns_amend1.csv", index=False)
    mrows_df.to_csv(A / "signals_S5_S7_monthly_targets.csv", index=False)
    for n in NEW_NAMES:
        r = results[("primary", False, n)]
        pd.DataFrame(r.trades).to_csv(A / f"trades_{n}.csv", index=False)
        r.nav.to_csv(A / f"nav_{n}.csv", index=False)
    # bootstrap (S5-S7)
    seed, nres, blk = 20261019, 2000, 21
    br = []
    for per in pers:
        idx = stationary_bootstrap_indices(len(ex[("primary", False, "S1_WEEKLY_MOM", per)]), blk, nres, seed)
        for s in NEW_NAMES:
            for ref in ("EW_SECTOR", "CSI300_BH"):
                pt, lo, hi = bootstrap_sharpe_diff(ex[("primary", False, s, per)], ex[("primary", False, ref, per)], idx, 0.90)
                br.append({"period": per, "strategy": s, "reference": ref, "xsharpe_diff": pt, "ci90_lo": lo, "ci90_hi": hi,
                           "ci_includes_0": bool(lo <= 0 <= hi)})
    BT = pd.DataFrame(br)
    BT.to_csv(A / "bootstrap_amend1.csv", index=False)
    pr = []
    for cname in ("primary", "sens"):
        var_d = float(np.var([sample_moments(ex[(cname, False, s, "DEV")])[0] for s in POOL], ddof=1))
        for s in POOL + ["EW_SECTOR", "CSI300_BH"]:
            e = ex[(cname, False, s, "DEV")]
            pr.append({"cost": cname, "strategy": s, "psr0_excess": psr(e, 0.0), "dsr_N7": dsr(e, 7, var_d) if s in POOL else float("nan"),
                       "sr0_annual": expected_max_sr(var_d, 7) * math.sqrt(252)})
    PD = pd.DataFrame(pr)
    PD.to_csv(A / "psr_dsr_N7.csv", index=False)
    sel = select_pool(M[M.lot_mode == "fractional1m"])
    (A / "selection_amend1.json").write_text(json.dumps(sel, indent=2))
    # independent checks
    chk = {}
    z5 = results[("zero", False, "S5_IND_MOM_12_1")].nav.nav.iloc[-1]
    rp = replay_gross(panel, curve, plans["S5_IND_MOM_12_1"], start_pos, end_pos)
    chk["S5_zero_cost_engine_final_nav"] = float(z5)
    chk["S5_open_to_open_replay_final_nav"] = float(rp)
    chk["S5_rel_diff"] = float(rp / z5 - 1)
    chk["S5_blocked_orders_zero_cost"] = {k: int(v) for k, v in results[("zero", False, "S5_IND_MOM_12_1")].counters.items()}
    rng = np.random.default_rng(7)
    meta = inc.set_index("ticker")
    dates_sel = list(mrows_df.signal_date)
    pick = [dates_sel[k] for k in rng.choice(len(dates_sel), 3, replace=False)]
    cl = {}
    ok_all = []
    for sd in pick:
        i = panel.pos[sd]
        best = {}
        for t in panel.tickers:
            if t == "510300":
                continue
            j = panel.col[t]
            c = panel.C[:i + 1, j]
            if np.isnan(c[-1]):
                continue
            first = int(np.argmax(~np.isnan(panel.C[:, j])))
            if i - first + 1 < MIN_AGE_AMEND:
                continue
            to = np.nan_to_num(panel.TO[max(0, i - 59):i + 1, j]).sum() / 60.0
            if to < 3e7:
                continue
            k = meta.loc[t, "index_key"]
            if k not in best or (to, -ord(t[0])) > (best[k][0], 0) and to > best[k][0]:
                best[k] = (to, t)
        ser = lambda t: pd.Series(panel.C[:i + 1, panel.col[t]]).ffill().to_numpy()
        sc = sorted([((ser(t)[-22] / ser(t)[-253] - 1), t) for _, t in best.values()], key=lambda x: (-x[0], x[1]))[:5]
        mine = sorted(t for _, t in sc)
        theirs = sorted(k for k in mrows_df[mrows_df.signal_date == sd]["S5_IND_MOM_12_1"].iloc[0].split(";") for k in [k.split(":")[0]] if k)
        ok_all.append(bool(mine == theirs))
        cl[sd] = {"match": mine == theirs, "n_deduped": len(best), "mine": mine, "engine": theirs}
    chk["S5_selection_recompute"] = cl
    (A / "checks_amend1.json").write_text(json.dumps(chk, indent=1))
    ctx = dict(M=M, BT=BT, PD=PD, sel=sel, chk=chk, start=start, end=end, pers=pers, first=panel.sessions[first_ep],
               n_first=int(mrows_df.n_eligible.iloc[0]), proto_sha=(Path(__file__).parent / "protocol_sector_mom_amendment1.sha256").read_text().strip(),
               ncash=results[("primary", False, "S5_IND_MOM_12_1")].nav.cash.iloc[0])
    write_summary(A, ctx)
    return ctx


def write_summary(A, c):
    M, sel = c["M"], c["sel"]
    g = lambda cost, lot, s, per: M[(M.cost == cost) & (M.lot_mode == lot) & (M.strategy == s) & (M.period == per)].iloc[0]
    S = ["S1_WEEKLY_MOM"] + NEW_NAMES + ["CSI300_BH", "EW_SECTOR"]
    L = ["# sector_mom amendment 1: S5-S7 on the development period", "",
         f"Amendment sha256 {c['proto_sha']} (verified at run time). Holdout not loaded. S5-S7 first execution: **{c['first']}** "
         f"(common DEV window {c['start']}..{c['end']}; before the first execution S5-S7 sit in SHIBOR cash). S5-S7 eligibility: age >= 253 sessions.", "",
         "## Selection over S1-S7", "", f"**Candidate: {sel['candidate']}**" if sel["candidate"] else "**No strategy qualifies.**", "",
         f"Qualifiers: {sel['qualifiers']}", ""]
    rows = []
    for s in POOL:
        for cost in ("primary", "sens"):
            x = sel["detail"][s][cost]
            rows.append([s, cost, f"{f3(x['xs_DEV'])} (EW {f3(x['xs_DEV_EW'])}, CSI {f3(x['xs_DEV_CSI'])})", x["C1_xs_gt_EW_and_CSI"],
                         f"{pc(x['mdd_DEV'])} (CSI {pc(x['mdd_DEV_CSI'])})", x["C2_mdd_lt_CSI"],
                         f"A {f3(x['xs_A'])}/{f3(x['xs_A_EW'])}; B {f3(x['xs_B'])}/{f3(x['xs_B_EW'])}", x["C3_xs_gt_EW_in_A_and_B"]])
    L += table(["strategy", "cost", "C1 DEV xSharpe", "C1", "C2 DEV MDD", "C2", "C3 A and B vs EW", "C3"], rows)
    L += ["", "## DEV main table (primary cost, 1,000,000 CNY)", ""]
    rows = []
    for s in S:
        r = g("primary", "fractional1m", s, "DEV")
        rows.append([s, pc(r.cagr), pc(r.ann_vol), f3(r.xsharpe), pc(r.max_drawdown), f"{pc(r.worst_year)} ({r.worst_year_label})", f"{r.ann_one_way_turnover:.2f}",
                     pc(r.cost_drag), int(r.n_round_trips), int(r.n_fills), pc(r.win_rate, 1), f"{r.avg_holding_days:.1f}", pc(r.mean_cash_weight),
                     f"{int(r.limit_blocked_buy)}/{int(r.limit_blocked_sell)}"])
    L += table(["strategy", "CAGR", "vol", "xSharpe", "MDD", "worst yr", "turnover/yr", "cost drag", "round trips", "fills", "win", "avg hold d", "mean cash", "lim-blk b/s"], rows)
    L += ["", "## xSharpe by period (primary / sensitivity)", ""]
    L += table(["strategy", "DEV", "DEV_A", "DEV_B"], [[s] + [f"{f3(g('primary','fractional1m',s,p).xsharpe)} / {f3(g('sens','fractional1m',s,p).xsharpe)}" for p in ("DEV", "DEV_A", "DEV_B")] for s in S])
    L += ["", "## Sensitivity cost DEV and 100k lots DEV (primary)", ""]
    L += table(["strategy", "sens CAGR", "sens xSharpe", "sens MDD", "lots CAGR", "lots xSharpe", "lots MDD"],
               [[s, pc(g('sens','fractional1m',s,'DEV').cagr), f3(g('sens','fractional1m',s,'DEV').xsharpe), pc(g('sens','fractional1m',s,'DEV').max_drawdown),
                 pc(g('primary','lots100k',s,'DEV').cagr), f3(g('primary','lots100k',s,'DEV').xsharpe), pc(g('primary','lots100k',s,'DEV').max_drawdown)] for s in S])
    L += ["", "## Bootstrap 90% CI of xSharpe difference (primary)", ""]
    L += table(["period", "strategy", "vs", "diff", "lo", "hi", "incl 0"], [[r.period, r.strategy, r.reference, f"{r.xsharpe_diff:+.3f}", f"{r.ci90_lo:+.3f}", f"{r.ci90_hi:+.3f}", r.ci_includes_0] for r in c["BT"].itertuples()])
    L += ["", "## PSR(0) and DSR (N = 7)", ""]
    L += table(["cost", "strategy", "PSR(0)", "DSR N=7", "SR0 ann"], [[r.cost, r.strategy, f3(r.psr0_excess), f3(r.dsr_N7), f3(r.sr0_annual)] for r in c["PD"].itertuples()])
    L += ["", "## Independent checks", "", "```", json.dumps(c["chk"], indent=1), "```", "",
          "## Notes", "", "- S5-S7 run on the common DEV windows (not shifted to their own start); targets are computed on forward-filled closes, top 5 by score from the deduplicated eligible set (age >= 253), equal weight 1/5. "
          "S7 filters the already-selected top 5 by the SHIBOR cash return over the same 252-to-21 window; filtered slots stay cash.",
          "- Weekly-rebalance mechanics (delta trades, sells first, blocked-order retry) are the same as S1."]
    (Path(A) / "summary_amendment1.md").write_text("\n".join(L) + "\n")


if __name__ == "__main__":
    run()
