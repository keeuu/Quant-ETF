"""Phase 1 (DEV sanity) of the HYBRID protocol. No holdout data is loaded (guard active)."""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

from research_v3.stats import bootstrap_sharpe_diff, stationary_bootstrap_indices

from . import guard
from .engine import StratSpec, simulate
from .hybrid import CAPITAL, COSTS, benchmark_plans, build_plans, groups_of, prepare
from .metrics import calendar_year_table, daily_rf, slice_metrics
from .report import f3, pc, table

HERE = Path(__file__).resolve().parent
ART = HERE / "artifacts_hybrid_dev"
STRATS = ["HYBRID", "CAP_DEF_M_CN", "CSI300_BH", "CN_60_40", "EW_SECTOR"]


def simulate_all(ctx, cost_name, replace=True, start=None):
    panel, curve = ctx["panel"], ctx["curve"]
    hyb, cap, rows, start_pos, end_pos = build_plans(ctx, replace)
    bh, c6040, ew = benchmark_plans(ctx, cap)
    g = groups_of(ctx)
    plans = {"HYBRID": (StratSpec("HYBRID", "mom"), hyb), "CAP_DEF_M_CN": (StratSpec("CAP_DEF_M_CN", "mom"), cap),
             "CSI300_BH": (StratSpec("CSI300_BH", "mom"), bh), "CN_60_40": (StratSpec("CN_60_40", "mom"), c6040),
             "EW_SECTOR": (StratSpec("EW_SECTOR", "ew"), ew)}
    out = {n: simulate(panel, pl, sp, COSTS[cost_name], CAPITAL, False, curve, start_pos, end_pos, postpone_on_missing=True, groups=g)
           for n, (sp, pl) in plans.items()}
    return out, rows, start_pos, end_pos


def run(out_dir: Path = ART, log=print) -> dict:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    ctx = prepare(guard.DEV_END, log)
    panel = ctx["panel"]
    results, wrows, start_pos, end_pos = {}, None, None, None
    for cn in ("primary", "sens", "zero"):
        results[cn], wrows, start_pos, end_pos = simulate_all(ctx, cn)
    start, end = panel.sessions[start_pos], panel.sessions[end_pos]
    assert end <= guard.DEV_END
    dates = panel.sessions[start_pos:end_pos + 1]
    rf = daily_rf(dates, ctx["curve"])
    first_exec = panel.sessions[ctx_first_exec(wrows, panel)]
    rows, ex, cyrows = [], {}, []
    for cn, res in results.items():
        for s, r in res.items():
            mt, e = slice_metrics(r, rf, start, end, CAPITAL, include_counters=True)
            ex[(cn, s)] = e
            gw = r.group_w
            mt.update(mean_equity=float(gw.equity.mean()), mean_gold=float(gw.gold.mean()), mean_bond=float(gw.bond.mean()),
                      mean_cash=mt["mean_cash_weight"], n_postponed=len(r.postponements))
            rows.append({"cost": cn, "strategy": s, **mt})
            for y, v in calendar_year_table(r.nav).iterrows():
                cyrows.append({"cost": cn, "strategy": s, "year": y, "return": v["ret"], "full_year": v["full_year"]})
    M = pd.DataFrame(rows)
    for cn in ("primary", "sens"):
        for s in STRATS:
            z = M[(M.cost == "zero") & (M.strategy == s)].cagr.iloc[0]
            m = (M.cost == cn) & (M.strategy == s)
            M.loc[m, "cost_drag"] = z - M.loc[m, "cagr"]
    M.to_csv(out_dir / "metrics_dev.csv", index=False)
    pd.DataFrame(cyrows).to_csv(out_dir / "calendar_year_returns_dev.csv", index=False)
    wrows.to_csv(out_dir / "monthly_targets_dev.csv", index=False)
    for s, r in results["primary"].items():
        pd.DataFrame(r.trades).to_csv(out_dir / f"trades_{s}.csv", index=False)
        r.nav.to_csv(out_dir / f"nav_{s}.csv", index=False)
        pd.DataFrame(r.postponements).to_csv(out_dir / f"postponements_{s}.csv", index=False)
    # bootstrap HYBRID - CAP_DEF_M_CN (DEV, informational)
    idx = stationary_bootstrap_indices(len(ex[("primary", "HYBRID")]), 21, 2000, 20261019)
    bt = {}
    for cn in ("primary", "sens"):
        pt, lo, hi = bootstrap_sharpe_diff(ex[(cn, "HYBRID")], ex[(cn, "CAP_DEF_M_CN")], idx, 0.90)
        bt[cn] = dict(diff=pt, lo=lo, hi=hi, includes_0=bool(lo <= 0 <= hi))
    # engine identity check: replacement disabled -> HYBRID == CAP_DEF_M_CN
    off, _, _, _ = simulate_all(ctx, "primary", replace=False)
    ident = float(np.max(np.abs(off["HYBRID"].nav.nav.to_numpy() - results["primary"]["CAP_DEF_M_CN"].nav.nav.to_numpy())))
    # independent recompute of HYBRID targets on 3 random DEV months
    chk = independent_check(ctx, wrows)
    chk["replacement_off_max_abs_nav_diff_vs_CAP_DEF_M_CN"] = ident
    chk["bootstrap_HYBRID_minus_CAP"] = bt
    chk["first_execution"] = first_exec
    chk["n_monthly_rebalances_in_dev"] = int(len(wrows))
    (out_dir / "checks_dev.json").write_text(json.dumps(chk, indent=1, default=str))
    write_summary(out_dir, M, chk, wrows, ctx, start, end)
    return dict(M=M, chk=chk)


def ctx_first_exec(wrows, panel):
    return panel.pos[wrows.execution_date.iloc[0]]


def independent_check(ctx, wrows):
    panel = ctx["panel"]
    meta = ctx["inc"].set_index("ticker")
    rng = np.random.default_rng(11)
    pick = [wrows.signal_date.iloc[k] for k in rng.choice(len(wrows), 3, replace=False)]
    out = {}
    for sd in pick:
        r = wrows[wrows.signal_date == sd].iloc[0]
        cap = json.loads(r.cap_weights)
        E = sum(cap.get(t, 0) for t in EQUITY_T)
        i = panel.pos[sd]
        best = {}
        for t in ctx["sector"]:
            j = panel.col[t]
            c = panel.C[:i + 1, j]
            if np.isnan(c[-1]):
                continue
            first = int(np.argmax(~np.isnan(panel.C[:, j])))
            if i - first + 1 < 253:
                continue
            to = np.nan_to_num(panel.TO[max(0, i - 59):i + 1, j]).sum() / 60.0
            if to < 3e7:
                continue
            k = meta.loc[t, "index_key"]
            if k not in best or to > best[k][0] or (to == best[k][0] and t < best[k][1]):
                best[k] = (to, t)
        ser = lambda t: pd.Series(panel.C[:i + 1, panel.col[t]]).ffill().to_numpy()
        sc = sorted([((ser(t)[-22] / ser(t)[-253] - 1), t) for _, t in best.values()], key=lambda x: (-x[0], x[1]))[:5]
        exp = {t: w for t, w in cap.items() if t not in EQUITY_T}
        if E > 1e-12:
            if sc:
                for _, t in sc:
                    exp[t] = E / len(sc)
            else:
                exp.update({t: w for t, w in cap.items() if t in EQUITY_T})
        got = json.loads(r.hybrid_weights)
        ok = set(exp) == set(got) and all(abs(exp[k] - got[k]) < 1e-9 for k in exp)
        out[sd] = {"match": bool(ok), "E": E, "n_sector": len(sc), "sum_weights": float(sum(got.values())), "cash": 1 - float(sum(got.values()))}
    return {"independent_recompute": out}


EQUITY_T = ("510300", "510050", "510500", "159915")


def write_summary(out_dir, M, chk, wrows, ctx, start, end):
    L = ["# HYBRID phase 1 (DEV sanity check)", "", f"Protocol sha256 {(Path(__file__).parent / 'protocol_hybrid.sha256').read_text().strip()} verified at run time. "
         f"Window {start}..{end}; first execution {chk['first_execution']}. Holdout not loaded. No decision depends on this phase.", ""]
    for cn, title in (("primary", "Primary cost 2.5+10 bp"), ("sens", "Sensitivity cost 2.5+20 bp")):
        L += [f"## DEV metrics: {title}", ""]
        rows = []
        for s in STRATS:
            r = M[(M.cost == cn) & (M.strategy == s)].iloc[0]
            rows.append([s, pc(r.cagr), pc(r.ann_vol), f3(r.xsharpe), pc(r.max_drawdown), f"{r.ann_one_way_turnover:.2f}", pc(r.cost_drag),
                         pc(r.mean_equity, 1), pc(r.mean_gold, 1), pc(r.mean_bond, 1), pc(r.mean_cash, 1), f"{int(r.n_round_trips)} ({int(r.n_fills)})",
                         f"{int(r.limit_blocked_buy)}/{int(r.limit_blocked_sell)}", int(r.n_postponed)])
        L += table(["strategy", "CAGR", "vol", "xSharpe", "MDD", "turnover/yr", "cost drag", "eq wt", "gold wt", "bond wt", "cash wt", "round trips (fills)", "lim-blk b/s", "postponed days"], rows)
        L.append("")
    p = M[M.cost == "primary"].set_index("strategy").xsharpe
    s_ = M[M.cost == "sens"].set_index("strategy").xsharpe
    b = chk["bootstrap_HYBRID_minus_CAP"]
    L += ["## HYBRID minus CAP_DEF_M_CN (DEV xSharpe)", "", f"- primary: {p['HYBRID'] - p['CAP_DEF_M_CN']:+.3f} (bootstrap 90% CI [{b['primary']['lo']:+.3f}, {b['primary']['hi']:+.3f}])",
          f"- sensitivity: {s_['HYBRID'] - s_['CAP_DEF_M_CN']:+.3f} (CI [{b['sens']['lo']:+.3f}, {b['sens']['hi']:+.3f}])", "",
          "## Checks", "", "```", json.dumps({k: v for k, v in chk.items() if k != 'bootstrap_HYBRID_minus_CAP'}, indent=1, default=str), "```"]
    (Path(out_dir) / "summary.md").write_text("\n".join(L) + "\n")


if __name__ == "__main__":
    run()
