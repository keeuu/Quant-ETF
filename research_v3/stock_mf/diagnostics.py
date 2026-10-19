"""Single-factor decomposition on the development period. 诊断用途，不用于选择因子 (amendment 1 diagnostics_policy).
Usage: python -m research_v3.stock_mf.diagnostics"""
from __future__ import annotations

import time

import numpy as np
import pandas as pd

from .engine import Costs
from .factors import FACTOR_NAMES, composite
from .metrics import period_metrics
from .panel import build_dev_panel
from .protocol import load_verified_amendment1
from .runner import HERE, PERIODS, Context

OUT = HERE / "artifacts_dev" / "diagnostics"
NOTE = "诊断用途，不用于选择因子"
PER_YEAR = 26.0


def spearman_rows(a, b):
    return float(pd.Series(a).rank().corr(pd.Series(b).rank()))


def main():
    t0 = time.time()
    load_verified_amendment1()
    OUT.mkdir(parents=True, exist_ok=True)
    panel = build_dev_panel()
    cache: dict = {}
    base = Context(panel, "biweekly", 20, fac_cache=cache)
    sig = base.signals
    sess = panel.sessions
    # ---------------------------------------------------------------- long-only top-10 per factor
    rows = []
    rf_by_date = dict(zip(sess[base.start:base.end + 1], base.rf[base.start:base.end + 1]))
    variants = {"COMPOSITE": None, **{f: [f] for f in FACTOR_NAMES}}
    for name, names in variants.items():
        ctx = base if names is None else Context(panel, "biweekly", 20, names=names, fac_cache=cache, univs=base.univs)
        for acct, costs, lots, cap in [("SMALL_NOMIN_10", Costs(2.5, 0, 0.1, 10), True, 25_000), ("LARGE_10", Costs(2.5, 0, 0.1, 10), False, 1e6)]:
            r = ctx.run("strategy", costs, lots, cap, f"{name}_{acct}")
            rf = np.array([rf_by_date[d] for d in r.nav["date"]])
            for per, (a, b) in PERIODS.items():
                m, _, _ = period_metrics(r.nav, rf, a if a > r.nav["date"].iloc[0] else r.nav["date"].iloc[0], b,
                                         base_nav=None if a <= r.nav["date"].iloc[0] else None)
                rows.append({"factor": name, "run": acct, "period": per, "cagr": m["cagr"], "xsharpe": m["xsharpe"],
                             "max_drawdown": m["max_drawdown"], "ann_one_way_turnover": m["ann_one_way_turnover"],
                             "cost_drag_ann": m["cost_drag_ann"], "note": NOTE})
        print("top10", name, round(time.time() - t0), "s", flush=True)
    pd.DataFrame(rows).to_csv(OUT / "top10_by_factor.csv", index=False)

    # ---------------------------------------------------------------- quintiles, IC, correlations
    q_rows, ic_rows, corr_acc = [], [], []
    adjff = panel.adj_close_ff
    for k, s in enumerate(sig[:-1]):
        s2 = sig[k + 1]
        mem = base.members[s]
        fac = cache[s]
        ret = adjff[s2] / adjff[s] - 1.0
        ok4 = mem & np.all([np.isfinite(fac[f]) for f in FACTOR_NAMES], axis=0) & np.isfinite(ret)
        if ok4.sum() < 50:
            continue
        scores = {f: fac[f] for f in FACTOR_NAMES}
        scores["COMPOSITE"] = composite(fac, mem)
        idx = np.where(ok4)[0]
        cm = pd.DataFrame({f: fac[f][idx] for f in FACTOR_NAMES}).corr(method="spearman").to_numpy()
        corr_acc.append(cm)
        for f, v in scores.items():
            x = v[idx]
            rk = pd.Series(x).rank(method="first").to_numpy()
            q = np.minimum((rk - 1) * 5 // len(x), 4).astype(int)
            rr = ret[idx]
            row = {"date": sess[s], "year": int(sess[s][:4]), "factor": f, "ic": spearman_rows(x, rr)}
            for b in range(5):
                row[f"Q{b + 1}"] = float(rr[q == b].mean())
            q_rows.append(row)
    qd = pd.DataFrame(q_rows)
    qd["spread"] = qd["Q5"] - qd["Q1"]
    qd.to_csv(OUT / "per_period_quintile_ic.csv", index=False)
    pd.DataFrame(np.mean(corr_acc, axis=0), index=FACTOR_NAMES, columns=FACTOR_NAMES).assign(note=NOTE).to_csv(OUT / "factor_correlation.csv")
    out_q, out_ic = [], []
    for f in ["COMPOSITE", *FACTOR_NAMES]:
        for per, (a, b) in PERIODS.items():
            d = qd[(qd.factor == f) & (qd.date >= a) & (qd.date <= b)]
            if d.empty:
                continue
            n = len(d)
            yrs = n / PER_YEAR
            r = {"factor": f, "period": per, "n_periods": n, "note": NOTE}
            for qn in ["Q1", "Q2", "Q3", "Q4", "Q5"]:
                r[f"{qn}_ann"] = float(np.prod(1 + d[qn]) ** (1 / yrs) - 1)
            r["Q5_minus_Q1_ann_geo"] = r["Q5_ann"] - r["Q1_ann"]
            r["spread_mean_per_period"] = float(d["spread"].mean())
            r["spread_t"] = float(d["spread"].mean() / (d["spread"].std(ddof=1) / np.sqrt(n)))
            out_q.append(r)
            out_ic.append({"factor": f, "period": per, "n_periods": n, "mean_ic": float(d["ic"].mean()),
                           "ic_t": float(d["ic"].mean() / (d["ic"].std(ddof=1) / np.sqrt(n))), "pct_ic_pos": float((d["ic"] > 0).mean()),
                           "note": NOTE})
    pd.DataFrame(out_q).to_csv(OUT / "quintiles.csv", index=False)
    pd.DataFrame(out_ic).to_csv(OUT / "rank_ic.csv", index=False)
    yr = qd.groupby(["factor", "year"]).apply(lambda g: np.prod(1 + g["Q5"]) - np.prod(1 + g["Q1"])).unstack("factor")
    yr["note"] = NOTE
    yr.to_csv(OUT / "spread_by_year.csv")
    print("done", round(time.time() - t0), "s")


if __name__ == "__main__":
    main()
