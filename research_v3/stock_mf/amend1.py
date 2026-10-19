"""Amendment 1 (LOWTURN_MONTHLY) development run: monthly signals, buffer rank <= 30. Usage: python -m research_v3.stock_mf.amend1"""
from __future__ import annotations

import json
import time

import numpy as np
import pandas as pd

from research_v3 import stats

from . import guard
from .engine import Costs
from .metrics import index_nav_frame, period_metrics
from .panel import build_dev_panel
from .protocol import load_verified_amendment1
from .runner import (BOOT_BLOCK, BOOT_N, BOOT_SEED, DEV_END, HERE, PERIODS, Context, decide, load_index_close)

OUT = HERE / "artifacts_dev" / "amend1"
KEEP_RANK_AMEND = 30
SPEC = [("LM_SMALL_NOMIN_10", Costs(2.5, 0.0, 0.1, 10), True, 25_000), ("LM_SMALL_NOMIN_20", Costs(2.5, 0.0, 0.1, 20), True, 25_000),
        ("LM_SMALL_MIN5_10", Costs(2.5, 5.0, 0.1, 10), True, 25_000), ("LM_SMALL_MIN5_20", Costs(2.5, 5.0, 0.1, 20), True, 25_000),
        ("LM_LARGE_10", Costs(2.5, 0.0, 0.1, 10), False, 1_000_000), ("LM_LARGE_20", Costs(2.5, 0.0, 0.1, 20), False, 1_000_000)]


def main():
    t0 = time.time()
    amend = load_verified_amendment1()
    OUT.mkdir(parents=True, exist_ok=True)
    panel = build_dev_panel()
    cache: dict = {}
    ctx_b = Context(panel, "biweekly", 20, fac_cache=cache)
    ctx_m = Context(panel, "monthly", KEEP_RANK_AMEND, fac_cache=cache, univs=ctx_b.univs)
    print("signals monthly", len(ctx_m.signals), "biweekly", len(ctx_b.signals), flush=True)
    runs = {n: ctx_m.run("strategy", c, l, cap, n) for n, c, l, cap in SPEC}
    base = {"ORIG_SMALL_NOMIN_10": ctx_b.run("strategy", Costs(2.5, 0, 0.1, 10), True, 25_000, "o10"),
            "ORIG_SMALL_MIN5_10": ctx_b.run("strategy", Costs(2.5, 5.0, 0.1, 10), True, 25_000, "o5"),
            "ORIG_LARGE_10": ctx_b.run("strategy", Costs(2.5, 0, 0.1, 10), False, 1e6, "ol"),
            "EW_UNIVERSE_10": ctx_b.run("ew", Costs(2.5, 0, 0.1, 10), False, 1e6, "e10"),
            "EW_UNIVERSE_20": ctx_b.run("ew", Costs(2.5, 0, 0.1, 20), False, 1e6, "e20")}
    allruns = {**runs, **base}
    sess = panel.sessions[ctx_m.start:ctx_m.end + 1]
    rf_by_date = dict(zip(sess, ctx_m.rf[ctx_m.start:ctx_m.end + 1]))
    bench = {nm: index_nav_frame(load_index_close(c), sess) for c, nm in [("000852", "CSI1000"), ("000905", "CSI500")]}
    navs = {k: r.nav for k, r in allruns.items()}
    for k, v in bench.items():
        navs[k] = v.assign(cash=0.0, n_hold=0)
    rows, store = [], {}
    for name, nav in navs.items():
        rf = np.array([rf_by_date[d] for d in nav["date"]])
        for per, (a, b) in PERIODS.items():
            if nav["date"].iloc[0] > b:
                continue
            start, bn = max(a, nav["date"].iloc[0]), None
            if nav["date"].iloc[0] > a:
                bn, start = float(nav["nav"].iloc[0]), nav["date"].iloc[1]
            m, ret, cy = period_metrics(nav, rf, start, b, base_nav=bn)
            msk = ((nav["date"] >= start) & (nav["date"] <= b)).to_numpy()
            store[(name, per)] = (nav["date"][msk].tolist(), ret, rf[msk])
            row = {"run": name, "period": per, **m}
            if per == "DEV" and name in allruns:
                c = allruns[name].counters
                row.update(delisted_holdings=c["delisted_holdings"], blocked_buy=c["blocked_buy"], blocked_sell=c["blocked_sell"],
                           signals=c["signals"])
            rows.append(row)
    c1000_start = bench["CSI1000"]["date"].iloc[1]
    for name in ["LM_SMALL_NOMIN_10", "LM_SMALL_NOMIN_20", "EW_UNIVERSE_10", "EW_UNIVERSE_20", "CSI1000"]:
        nav = navs[name]
        rf = np.array([rf_by_date[d] for d in nav["date"]])
        bn = float(nav["nav"].iloc[0]) if name == "CSI1000" else None
        m, _, _ = period_metrics(nav, rf, c1000_start, DEV_END, base_nav=bn)
        rows.append({"run": name, "period": "DEV_OVERLAP_CSI1000", **m})
    metrics = pd.DataFrame(rows)
    metrics.to_csv(OUT / "metrics.csv", index=False)
    cyr = []
    for name in navs:
        d, r, _ = store[(name, "DEV")]
        s = pd.Series(r + 1.0, index=pd.to_datetime(d))
        for y, v in (s.groupby(s.index.year).prod() - 1).items():
            cyr.append({"run": name, "year": int(y), "ret": float(v)})
    pd.DataFrame(cyr).pivot(index="year", columns="run", values="ret").to_csv(OUT / "calendar_year_returns.csv")
    pd.DataFrame(runs["LM_SMALL_NOMIN_10"].trades).to_csv(OUT / "trades_LM_SMALL_NOMIN_10.csv", index=False)
    pd.DataFrame(ctx_m.reb_log["LM_SMALL_NOMIN_10"]).to_csv(OUT / "holdings_per_rebalance_LM_SMALL_NOMIN_10.csv", index=False)
    pd.DataFrame(ctx_m.reb_log["LM_LARGE_10"]).to_csv(OUT / "holdings_per_rebalance_LM_LARGE_10.csv", index=False)

    boot, cache_idx = [], {}

    def series_of(n, per):
        if per == "OV":
            d, r, f = store[(n, "DEV")]
            k = [i for i, x in enumerate(d) if x >= c1000_start]
            return [d[i] for i in k], r[k], f[k]
        return store[(n, per)]

    def bd(label, a, b, per):
        da, ra, _ = series_of(a, per)
        db, rb, _ = series_of(b, per)
        common = sorted(set(da) & set(db))
        ia, ib = {d: k for k, d in enumerate(da)}, {d: k for k, d in enumerate(db)}
        xa = np.array([ra[ia[d]] - rf_by_date[d] for d in common])
        xb = np.array([rb[ib[d]] - rf_by_date[d] for d in common])
        if len(common) not in cache_idx:
            cache_idx[len(common)] = stats.stationary_bootstrap_indices(len(common), BOOT_BLOCK, BOOT_N, BOOT_SEED)
        pt, lo, hi = stats.bootstrap_sharpe_diff(xa, xb, cache_idx[len(common)], 0.90)
        boot.append({"comparison": label, "period": "DEV_OVERLAP_CSI1000" if per == "OV" else per, "n_days": len(common),
                     "xsharpe_diff": pt, "ci90_lo": lo, "ci90_hi": hi, "ci_excludes_0": bool(lo > 0 or hi < 0)})
    for per in PERIODS:
        bd("LM_SMALL_NOMIN_10 - EW_UNIVERSE_10", "LM_SMALL_NOMIN_10", "EW_UNIVERSE_10", per)
        bd("LM_SMALL_NOMIN_20 - EW_UNIVERSE_20", "LM_SMALL_NOMIN_20", "EW_UNIVERSE_20", per)
        bd("LM_LARGE_10 - EW_UNIVERSE_10", "LM_LARGE_10", "EW_UNIVERSE_10", per)
        bd("LM_SMALL_NOMIN_10 - ORIG_SMALL_NOMIN_10", "LM_SMALL_NOMIN_10", "ORIG_SMALL_NOMIN_10", per)
    bd("LM_SMALL_NOMIN_10 - CSI1000", "LM_SMALL_NOMIN_10", "CSI1000", "OV")
    bd("LM_SMALL_NOMIN_20 - CSI1000", "LM_SMALL_NOMIN_20", "CSI1000", "OV")
    pd.DataFrame(boot).to_csv(OUT / "bootstrap.csv", index=False)

    # PSR(0) and DSR (N = 2 trials: original biweekly and LOWTURN_MONTHLY, SMALL/NO_MIN/10bp)
    ex = {n: store[(n, "DEV")][1] - store[(n, "DEV")][2] for n in ["LM_SMALL_NOMIN_10", "ORIG_SMALL_NOMIN_10"]}
    srs = [stats.sample_moments(v)[0] for v in ex.values()]
    var_sr = float(np.var(srs, ddof=1))
    prow = []
    for n in ["LM_SMALL_NOMIN_10", "LM_SMALL_NOMIN_20", "LM_SMALL_MIN5_10", "LM_LARGE_10", "ORIG_SMALL_NOMIN_10", "EW_UNIVERSE_10"]:
        for per in PERIODS:
            d, r, f = store[(n, per)]
            prow.append({"run": n, "period": per, "psr0": stats.psr(r - f, 0.0),
                         "dsr_N2": stats.dsr(r - f, 2, var_sr) if per == "DEV" and n.startswith("LM_") else np.nan})
    pd.DataFrame(prow).to_csv(OUT / "psr_dsr.csv", index=False)

    def mr(run, per):
        return metrics[(metrics["run"] == run) & (metrics["period"] == per)].iloc[0]
    dm = {"x_strat_10": mr("LM_SMALL_NOMIN_10", "DEV")["xsharpe"], "x_ew_10": mr("EW_UNIVERSE_10", "DEV")["xsharpe"],
          "x_strat_20": mr("LM_SMALL_NOMIN_20", "DEV")["xsharpe"], "x_ew_20": mr("EW_UNIVERSE_20", "DEV")["xsharpe"],
          "x_csi1000_overlap": mr("CSI1000", "DEV_OVERLAP_CSI1000")["xsharpe"],
          "x_strat_csi1000_overlap": mr("LM_SMALL_NOMIN_10", "DEV_OVERLAP_CSI1000")["xsharpe"],
          "x_strat20_csi1000_overlap": mr("LM_SMALL_NOMIN_20", "DEV_OVERLAP_CSI1000")["xsharpe"],
          "xA_strat": mr("LM_SMALL_NOMIN_10", "DEV_A")["xsharpe"], "xA_ew": mr("EW_UNIVERSE_10", "DEV_A")["xsharpe"],
          "xB_strat": mr("LM_SMALL_NOMIN_10", "DEV_B")["xsharpe"], "xB_ew": mr("EW_UNIVERSE_10", "DEV_B")["xsharpe"],
          "mdd_strat": mr("LM_SMALL_NOMIN_10", "DEV")["max_drawdown"], "mdd_ew": mr("EW_UNIVERSE_10", "DEV")["max_drawdown"]}
    v = decide(dm)
    v["inputs"] = {k: float(x) for k, x in dm.items()}
    v["dev_mdd_within_40pct"] = bool(dm["mdd_strat"] <= 0.40)
    v["dsr_var_sr_daily"] = var_sr
    (OUT / "verdict.json").write_text(json.dumps(v, indent=1))
    print(json.dumps(v, indent=1), round(time.time() - t0), "s")


if __name__ == "__main__":
    main()
