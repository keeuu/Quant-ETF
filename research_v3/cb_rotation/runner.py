"""cb_rotation development run: builds the panel, runs every strategy version and benchmark on 2018-2023, applies the
decision rule mechanically, writes artifacts_dev/. Usage: python -m research_v3.cb_rotation.runner
The holdout (2024-01-01 ..) is never loaded: all loaders clip to guard.DEV_END (2023-12-31)."""
from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np
import pandas as pd

from research_v3 import stats
from research_v3.cn_transfer.signals_cn import ShiborCurve
from research_v3.stock_mf import panel as smp
from research_v3.stock_mf.metrics import index_nav_frame, period_metrics

from . import data as cbdata
from . import guard
from .classify import classify_all
from .engine import Costs, eligibility, simulate, weekly_signal_rows
from .protocol import load_verified

HERE = Path(__file__).resolve().parent
OUT = HERE / "artifacts_dev"
DEV_START = "2018-01-01"
PERIODS = {"DEV": ("2018-01-01", "2023-12-31"), "DEV_A": ("2018-01-01", "2020-12-31"), "DEV_B": ("2021-01-01", "2023-12-31")}
BOOT_BLOCK, BOOT_N, BOOT_SEED = 21, 2000, 20261019
SMALL, LARGE = 25_000.0, 1_000_000.0
RUNS = {      # name: (capital, lots, costs, passive_frac, mode)
    "SMALL_DEC": (SMALL, True, Costs(), 0.0, "topn"),
    "SMALL_MIN5": (SMALL, True, Costs(min_commission=5.0), 0.0, "topn"),
    "SMALL_SLIP30": (SMALL, True, Costs(slippage_bp=30.0), 0.0, "topn"),
    "SMALL_PASS50": (SMALL, True, Costs(), 0.5, "topn"),
    "LARGE": (LARGE, False, Costs(), 0.0, "topn"),
    "EW_CB": (LARGE, False, Costs(), 0.0, "ew"),
    "SMALL_EXITEVT": (SMALL, True, Costs(), 0.0, "topn"),    # sensitivity only: forced exits ignore the liquidity filter
}
STRATS = ["SMALL_DEC", "SMALL_MIN5", "SMALL_SLIP30", "SMALL_PASS50", "SMALL_EXITEVT", "LARGE"]


def build_context(diag: dict | None = None):
    diag = diag if diag is not None else {}
    load_verified()
    guard.assert_date_allowed(PERIODS["DEV"][1])
    lst = cbdata.read_list()
    meta = cbdata.bond_meta(lst)
    cl = classify_all(lst, meta)
    delist_types = dict(zip(cl["code"], cl["type"]))
    # underlying stocks: local stock_mf data (clipped to the cb development end), reused by import
    sdiag = {}
    stocks = smp.load_dev_stocks(limit=guard.DEV_END, diagnostics=sdiag)
    stocks = [s for s in stocks if len(s.df) >= 60 and s.df.index.max() >= cbdata.PANEL_START]
    sp = smp.assemble(stocks)
    sess = [d for d in sp.sessions if cbdata.PANEL_START <= d <= guard.DEV_END]
    r0 = sp.sessions.index(sess[0])
    st_by_stock = {c: sp.st[r0:r0 + len(sess), j] for j, c in enumerate(sp.codes)}
    meta = meta[(meta["listing"] <= guard.DEV_END) & (meta["delist"].isna() | (meta["delist"] >= "2017-07-01"))]
    bonds = meta.to_dict("records")
    p = cbdata.build_panel(sess, bonds, st_by_stock, delist_types, diag=diag)
    diag["stock_panel"] = dict(sdiag, n_stocks=len(stocks))
    diag["terminated_in_window"] = int(p.terminated.sum())
    diag["terminated_without_type"] = [c for c, t, ty in zip(p.codes, p.terminated, p.delist_type) if t and not ty]
    return dict(lst=lst, meta=meta, cl=cl, p=p, diag=diag, sess=sess)


def accrual_for(sessions: list) -> np.ndarray:
    curve = ShiborCurve(cbdata.load_shibor())
    a = np.ones(len(sessions))
    for i in range(1, len(sessions)):
        a[i] = curve.accrual_factor(sessions[i - 1], sessions[i])
    return a


def nav_for_benchmark(close: pd.Series, sessions: list) -> pd.DataFrame:
    return index_nav_frame(close, sessions)


def run_all(ctx: dict, record: bool = True) -> dict:
    p = ctx["p"]
    E_full, E_exit = eligibility(p)
    _, E_exit_evt = eligibility(p, exit_liquidity=False)
    acc = accrual_for(p.sessions)
    start = next(i for i, d in enumerate(p.sessions) if d >= DEV_START)
    end = max(i for i, d in enumerate(p.sessions) if d <= guard.DEV_END)
    sig = weekly_signal_rows(p.sessions)
    sig = [s for s in sig if s >= start - 1]
    res = {}
    for name, (cap, lots, costs, pf, mode) in RUNS.items():
        t = time.time()
        res[name] = simulate(p, acc, start, end, sig, E_full, E_exit_evt if name == "SMALL_EXITEVT" else E_exit, cap, costs,
                             lots, mode=mode, passive_frac=pf, record_trades=record)
        print(f"  {name}: {time.time() - t:.1f}s end nav {res[name].nav['nav'].iloc[-1]:,.0f}", flush=True)
    ctx.update(E_full=E_full, E_exit=E_exit, acc=acc, start=start, end=end, sig=sig, res=res)
    return res


def metrics_tables(ctx: dict):
    p, acc, res, start, end = ctx["p"], ctx["acc"], ctx["res"], ctx["start"], ctx["end"]
    sessions = p.sessions
    rf_by_date = {d: acc[i] - 1.0 for i, d in enumerate(sessions)}
    frames = {n: r.nav for n, r in res.items()}
    cb = cbdata.load_cb_index()
    frames["CB_INDEX"] = nav_for_benchmark(cb, sessions)
    frames["CSI300"] = nav_for_benchmark(cbdata.load_csi300(), sessions)
    rows, cy_rows, store = [], [], {}
    for name, nav in frames.items():
        rf = np.array([rf_by_date.get(d, 0.0) for d in nav["date"]])
        for per, (a, b) in PERIODS.items():
            out, ret, cy = period_metrics(nav, rf, a, b)
            m = ((nav["date"] >= a) & (nav["date"] <= b)).to_numpy()
            dts = nav["date"][m].tolist()
            store[(name, per)] = (dts, ret, rf[m])
            row = dict(run=name, period=per, **out)
            if name in res:
                r = res[name]
                ev = pd.DataFrame(r.events) if r.events else pd.DataFrame(columns=["date", "kind"])
                tm = pd.DataFrame(r.terminal) if r.terminal else pd.DataFrame(columns=["date", "type"])
                inper = lambda df: df[(df["date"] >= a) & (df["date"] <= b)] if len(df) else df
                row["forced_exits"] = int((inper(ev)["kind"] == "forced_exits").sum()) if len(ev) else 0
                row["signal_ineligible_exits"] = int((inper(ev)["kind"] == "signal_ineligible_exits").sum()) if len(ev) else 0
                for k in ("passive", "redemption", "maturity", "unknown"):
                    row[f"{k}_delist_held"] = int((inper(tm)["type"] == k).sum()) if len(tm) else 0
                row["passive_loss_cny"] = float(sum(t["qty"] * t["last_close"] * (1 - t["fraction"]) for t in r.terminal
                                                    if a <= t["date"] <= b and t["type"] != "redemption" and t["type"] != "maturity"))
            rows.append(row)
            if per == "DEV":
                for y, v in cy.items():
                    cy_rows.append(dict(year=int(y), run=name, ret=float(v)))
    return pd.DataFrame(rows), pd.DataFrame(cy_rows), store, rf_by_date


def bootstrap_tables(store: dict, rf_by_date: dict):
    boot, psr_rows, cache = [], [], {}

    def diff(label, a, b, per):
        da, ra, _ = store[(a, per)]
        db, rb, _ = store[(b, per)]
        common = sorted(set(da) & set(db))
        ia, ib = {d: k for k, d in enumerate(da)}, {d: k for k, d in enumerate(db)}
        xa = np.array([ra[ia[d]] - rf_by_date[d] for d in common])
        xb = np.array([rb[ib[d]] - rf_by_date[d] for d in common])
        t = len(common)
        if t not in cache:
            cache[t] = stats.stationary_bootstrap_indices(t, BOOT_BLOCK, BOOT_N, BOOT_SEED)
        pt, lo, hi = stats.bootstrap_sharpe_diff(xa, xb, cache[t], 0.90)
        boot.append(dict(comparison=label, period=per, n_days=t, xsharpe_diff=pt, ci90_lo=lo, ci90_hi=hi,
                         ci_excludes_0=bool(lo > 0 or hi < 0)))

    for per in PERIODS:
        for s in ("SMALL_DEC", "LARGE"):
            diff(f"{s} - EW_CB", s, "EW_CB", per)
            diff(f"{s} - CB_INDEX", s, "CB_INDEX", per)
        diff("EW_CB - CB_INDEX", "EW_CB", "CB_INDEX", per)
    for nm in STRATS + ["EW_CB", "CB_INDEX", "CSI300"]:
        for per in PERIODS:
            d, r, f = store[(nm, per)]
            psr_rows.append(dict(run=nm, period=per, psr0=stats.psr(r - f, 0.0)))
    return pd.DataFrame(boot), pd.DataFrame(psr_rows)


def decision(metrics: pd.DataFrame) -> dict:
    def xs(run, per):
        return float(metrics[(metrics["run"] == run) & (metrics["period"] == per)]["xsharpe"].iloc[0])

    def mdd(run, per):
        return float(metrics[(metrics["run"] == run) & (metrics["period"] == per)]["max_drawdown"].iloc[0])

    d = {}
    dec = "SMALL_DEC"
    d["c1_dev_xs_gt_EW_CB"] = (xs(dec, "DEV"), xs("EW_CB", "DEV"), xs(dec, "DEV") > xs("EW_CB", "DEV"))
    d["c1_dev_xs_gt_CB_INDEX"] = (xs(dec, "DEV"), xs("CB_INDEX", "DEV"), xs(dec, "DEV") > xs("CB_INDEX", "DEV"))
    d["c2_devA_xs_gt_EW_CB"] = (xs(dec, "DEV_A"), xs("EW_CB", "DEV_A"), xs(dec, "DEV_A") > xs("EW_CB", "DEV_A"))
    d["c2_devB_xs_gt_EW_CB"] = (xs(dec, "DEV_B"), xs("EW_CB", "DEV_B"), xs(dec, "DEV_B") > xs("EW_CB", "DEV_B"))
    d["c3_dev_mdd_le_25pct"] = (mdd(dec, "DEV"), 0.25, mdd(dec, "DEV") <= 0.25)
    for tag, run in (("slip30", "SMALL_SLIP30"), ("min5", "SMALL_MIN5")):
        d[f"c4_{tag}_xs_gt_EW_CB"] = (xs(run, "DEV"), xs("EW_CB", "DEV"), xs(run, "DEV") > xs("EW_CB", "DEV"))
        d[f"c4_{tag}_xs_gt_CB_INDEX"] = (xs(run, "DEV"), xs("CB_INDEX", "DEV"), xs(run, "DEV") > xs("CB_INDEX", "DEV"))
    d = {k: dict(value=v[0], threshold_or_benchmark=v[1], passed=bool(v[2])) for k, v in d.items()}
    d["VERDICT"] = "PASS" if all(v["passed"] for v in d.values()) else "FAIL"
    return d


def universe_table(ctx: dict) -> pd.DataFrame:
    p, E_full, E_exit, sig = ctx["p"], ctx["E_full"], ctx["E_exit"], ctx["sig"]
    from .engine import double_low
    sc = double_low(p.close, p.premium)
    rows = []
    for s in sig:
        if p.sessions[s] < "2017-12-01" or p.sessions[s] > guard.DEV_END:
            continue
        rows.append(dict(date=p.sessions[s], bonds_with_bar=int(p.has_bar[s].sum()), eligible=int(E_full[s].sum()),
                         eligible_with_score=int((E_full[s] & np.isfinite(sc[s])).sum()),
                         eligible_without_premium=int((E_full[s] & ~np.isfinite(p.premium[s])).sum())))
    return pd.DataFrame(rows)


def forced_exit_causes(ctx: dict, run: str = "SMALL_DEC") -> pd.DataFrame:
    from .engine import horizon_dates
    p = ctx["p"]
    hz = horizon_dates(p.sessions)
    rows = []
    for e in ctx["res"][run].events:
        i, b = p.sessions.index(e["date"]), p.codes.index(e["code"])
        c = []
        if p.notice[b] <= e["date"]:
            c.append("redemption_notice")
        if p.st[i, b]:
            c.append("inferred_ST")
        if p.close_ff[i, b] > 150:
            c.append("close_gt_150")
        if p.maturity[b] <= hz[i]:
            c.append("maturity_within_20")
        rows.append(dict(date=e["date"], code=e["code"], kind=e["kind"], cause="+".join(c) or "liquidity_or_listing_filter"))
    return pd.DataFrame(rows)


def data_stats(ctx: dict) -> dict:
    from .fetch import DATA, need_codes
    lst, meta, cl = ctx["lst"], ctx["meta"], ctx["cl"]
    need = need_codes(lst)
    ok = [c for c in need if all((DATA / s / f"{c}.csv").exists() and (DATA / s / f"{c}.csv").stat().st_size > 50 for s in ("va", "sina", "tx"))]
    fails = json.loads((DATA / "failures.json").read_text()) if (DATA / "failures.json").exists() else {}
    all_meta = cbdata.bond_meta(lst)
    pre = all_meta[all_meta["delist"].notna() & (all_meta["delist"] < "2017-07-01")]
    return dict(n_list=len(lst), n_listed=len(all_meta) + int(lst["SECURITY_CODE"].str.startswith("404").sum()), n_404=int(lst["SECURITY_CODE"].str.startswith("404").sum()),
                n_live=int(all_meta["delist"].isna().sum()), n_delisted_total=int(all_meta["delist"].notna().sum()),
                n_stopped=len(cl), n_red=int((cl["type"] == "redemption").sum()), n_mat=int((cl["type"] == "maturity").sum()),
                n_pas=int((cl["type"] == "passive").sum()), n_pre2017=len(pre), n_need=len(need), n_ok=len(ok), n_fail=len(fails),
                fail_keys=sorted(fails)[:20])


def main():
    OUT.mkdir(exist_ok=True)
    t0 = time.time()
    ctx = build_context()
    print(f"panel {ctx['p'].close.shape} built in {time.time() - t0:.0f}s; diag={ctx['diag']}", flush=True)
    run_all(ctx)
    metrics, cy, store, rf_by_date = metrics_tables(ctx)
    boot, psr = bootstrap_tables(store, rf_by_date)
    dec = decision(metrics)
    uni = universe_table(ctx)
    res, p = ctx["res"], ctx["p"]
    metrics.to_csv(OUT / "metrics.csv", index=False)
    cy.pivot(index="year", columns="run", values="ret").to_csv(OUT / "calendar_year_returns.csv")
    boot.to_csv(OUT / "bootstrap.csv", index=False)
    psr.to_csv(OUT / "psr.csv", index=False)
    uni.to_csv(OUT / "universe_size.csv", index=False)
    (OUT / "decision.json").write_text(json.dumps(dec, indent=1, ensure_ascii=False))
    for n in STRATS:
        pd.DataFrame(res[n].trades).to_csv(OUT / f"trades_{n}.csv", index=False)
        res[n].nav.to_csv(OUT / f"nav_{n}.csv", index=False)
        pd.DataFrame(res[n].terminal).to_csv(OUT / f"terminal_{n}.csv", index=False)
    pd.DataFrame(res["SMALL_DEC"].holdings).to_csv(OUT / "holdings_weekly_SMALL_DEC.csv", index=False)
    pd.DataFrame(res["LARGE"].holdings).to_csv(OUT / "holdings_weekly_LARGE.csv", index=False)
    res["EW_CB"].nav.to_csv(OUT / "nav_EW_CB.csv", index=False)
    cl = ctx["cl"]
    cl[cl["last_bar"] <= guard.DEV_END].to_csv(OUT / "delist_classification_all.csv", index=False)   # development window only
    # passive delisting list for the development period (all bonds that stopped trading in 2018-2023), with held flags
    dev = cl[(cl["last_bar"] >= DEV_START) & (cl["last_bar"] <= guard.DEV_END)].copy()
    held = {n: {t["code"] for t in res[n].terminal} for n in ("SMALL_DEC", "LARGE")}
    dev["held_by_SMALL_DEC"] = dev["code"].isin(held["SMALL_DEC"])
    dev["held_by_LARGE"] = dev["code"].isin(held["LARGE"])
    dev[dev["type"] == "passive"].to_csv(OUT / "passive_delisting_list.csv", index=False)
    dev.to_csv(OUT / "delist_classification_dev.csv", index=False)
    from . import independent_check, verify_delist
    ctx["data_stats"] = data_stats(ctx)
    ctx["independent"] = independent_check.run(ctx, OUT)
    fe = forced_exit_causes(ctx)
    fe.to_csv(OUT / "forced_exit_causes_SMALL_DEC.csv", index=False)
    ctx["forced_causes"] = fe
    vp = OUT / "delist_verification.csv"
    if vp.exists():
        ctx["verification_text"] = verify_delist.summary_text(pd.read_csv(vp, dtype={"code": str, "reason_sh": str}),
                                                              __import__("research_v3.cb_rotation.classify", fromlist=["x"]).load_overrides())
    from .summary import deviations
    ctx["deviations"] = deviations(ctx)
    ctx["metrics"], ctx["cy"], ctx["boot"], ctx["psr"], ctx["dec"], ctx["uni"], ctx["dev_cl"] = metrics, cy, boot, psr, dec, uni, dev
    from .summary import write_summary
    write_summary(ctx, OUT)
    print(json.dumps({k: (v if k == "VERDICT" else v["passed"]) for k, v in dec.items()}, indent=1))
    print(f"done in {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
