"""Development-phase run for stock_mf: strategy under all protocol cost/account versions, benchmarks, partitions,
bootstrap, decision rule and artifacts. Usage: python -m research_v3.stock_mf.runner

Never reads data after guard.DEV_END (2024-03-31). The protocol hash is verified first."""
from __future__ import annotations

import json
import math
import time
from pathlib import Path

import numpy as np
import pandas as pd

from research_v3 import stats
from research_v3.cn_transfer.signals_cn import ShiborCurve

from . import guard
from .engine import Costs, simulate
from .factors import (KEEP_RANK, monthly_signals, TOP_N, affordable_mask, biweekly_signals, build_universes, composite, factors_at, rank_table,
                      select_targets, universe_for_signal)
from .metrics import index_nav_frame, period_metrics, xsharpe
from .panel import DATA_DIR, Panel, build_dev_panel
from .protocol import load_verified

HERE = Path(__file__).resolve().parent
OUT = HERE / "artifacts_dev"
SHIBOR_CSV = HERE.parent / "cn_transfer" / "data" / "shibor_3m.csv"
DEV_START, DEV_END = "2010-01-01", guard.DEV_END
PERIODS = {"DEV": ("2010-01-01", "2024-03-31"), "DEV_A": ("2010-01-01", "2016-12-31"), "DEV_B": ("2017-01-01", "2024-03-31")}
BOOT_BLOCK, BOOT_N, BOOT_SEED = 21, 2000, 20261019


def load_shibor_series() -> pd.Series:
    df = pd.read_csv(SHIBOR_CSV, dtype={"Date": str})
    df = guard.clip_to_dev(df, "Date")
    return pd.Series(df["shibor_3m_pct"].values, index=df["Date"].values, dtype=float)


def load_index_close(code: str) -> pd.Series:
    df = pd.read_csv(DATA_DIR / "index" / f"{code}.csv", dtype={"date": str})
    df = guard.clip_to_dev(df, "date")
    return pd.Series(df["close"].values, index=df["date"].values, dtype=float)


class Context:
    def __init__(self, panel: Panel, schedule: str = "biweekly", keep_rank: int = KEEP_RANK, names=None, fac_cache=None,
                 univs=None):
        self.p = panel
        S = len(panel.sessions)
        self.start = next(i for i, d in enumerate(panel.sessions) if d >= DEV_START)
        self.end = max(i for i, d in enumerate(panel.sessions) if d <= DEV_END)
        curve = ShiborCurve(load_shibor_series())
        self.accrual = np.ones(S)
        for i in range(1, S):
            self.accrual[i] = curve.accrual_factor(panel.sessions[i - 1], panel.sessions[i])
        self.rf = self.accrual - 1.0
        self.keep_rank = keep_rank
        self.univs = univs if univs is not None else build_universes(panel)
        sched = biweekly_signals if schedule == "biweekly" else monthly_signals
        self.signals = [s for s in sched(panel.sessions, self.univs, DEV_START) if self.start <= s < self.end]
        fac_cache = fac_cache if fac_cache is not None else {}
        self.tables, self.afford, self.members = {}, {}, {}
        for s in self.signals:
            mem = universe_for_signal(self.univs, s) & panel.has_bar[s]
            self.members[s] = mem
            if s not in fac_cache:
                fac_cache[s] = factors_at(panel, s)
            self.tables[s] = rank_table(composite(fac_cache[s], mem, names), panel.codes)
            self.afford[s] = affordable_mask(panel, s)
        self.reb_log: dict = {}

    def strategy_fn(self, small: bool, label: str):
        log = self.reb_log.setdefault(label, [])

        def fn(i, held, pv):
            table = self.tables[i]
            aff = self.afford[i] if small else None
            tgt = select_targets(table, held.keys(), keep_rank=self.keep_rank, affordable=aff)
            if aff is None:
                t = table
            else:
                t = table[aff[table["j"].to_numpy()]].copy()
                t["rank"] = np.arange(1, len(t) + 1)
            rk = dict(zip(t["j"], t["rank"]))
            slot = pv / TOP_N
            out = {}
            for j in tgt:
                out[j] = None if j in held else slot
                log.append(dict(signal=self.p.sessions[i], code=self.p.codes[j], rank=int(rk[j]),
                                action="keep" if j in held else "buy", price=float(self.p.close[i, j]),
                                universe_size=int(self.members[i].sum()), n_ranked=len(table)))
            for j in held:
                if j not in out:
                    log.append(dict(signal=self.p.sessions[i], code=self.p.codes[j], rank=int(rk.get(j, -1)), action="sell",
                                    price=float(self.p.close[i, j]), universe_size=int(self.members[i].sum()), n_ranked=len(table)))
            return out
        return fn

    def ew_fn(self):
        def fn(i, held, pv):
            js = np.where(self.members[i])[0]
            if len(js) == 0:
                return {}
            w = pv / len(js)
            return {int(j): w for j in js}
        return fn

    def run(self, kind: str, costs: Costs, lots: bool, capital: float, label: str):
        fn = self.strategy_fn(lots, label) if kind == "strategy" else self.ew_fn()
        return simulate(self.p, self.accrual, self.start, self.end, self.signals, fn, capital, costs, lots,
                        record_trades=(kind == "strategy"))


def decide(m: dict) -> dict:
    """Mechanical decision rule (protocol decision_on_development). m holds the needed xSharpe / MDD values."""
    c1 = m["x_strat_10"] > m["x_ew_10"] and m["x_strat_csi1000_overlap"] > m["x_csi1000_overlap"]
    c2 = m["xA_strat"] > m["xA_ew"] and m["xB_strat"] > m["xB_ew"]
    c3 = m["mdd_strat"] < m["mdd_ew"]
    c4 = m["x_strat_20"] > m["x_ew_20"] and m["x_strat20_csi1000_overlap"] > m["x_csi1000_overlap"]
    return {"c1_xsharpe_beats_ew_and_csi1000": bool(c1), "c2_beats_ew_in_both_halves": bool(c2),
            "c3_mdd_below_ew": bool(c3), "c4_c1_holds_at_20bp": bool(c4),
            "verdict": "PASS" if (c1 and c2 and c3 and c4) else "FAIL"}


def main():
    t0 = time.time()
    proto = load_verified()                       # refuses to run on hash mismatch
    OUT.mkdir(exist_ok=True)
    diag: dict = {}
    panel = build_dev_panel(diagnostics=diag)
    print("panel", panel.close.shape, panel.sessions[0], panel.sessions[-1], round(time.time() - t0), "s", flush=True)
    ctx = Context(panel)
    print("signals", len(ctx.signals), panel.sessions[ctx.signals[0]], panel.sessions[ctx.signals[-1]], round(time.time() - t0), "s", flush=True)

    runs = {}
    spec = [("STRAT_SMALL_NOMIN_10", "strategy", Costs(2.5, 0.0, 0.1, 10), True, 25_000),
            ("STRAT_SMALL_NOMIN_20", "strategy", Costs(2.5, 0.0, 0.1, 20), True, 25_000),
            ("STRAT_SMALL_MIN5_10", "strategy", Costs(2.5, 5.0, 0.1, 10), True, 25_000),
            ("STRAT_SMALL_MIN5_20", "strategy", Costs(2.5, 5.0, 0.1, 20), True, 25_000),
            ("STRAT_LARGE_10", "strategy", Costs(2.5, 0.0, 0.1, 10), False, 1_000_000),
            ("STRAT_LARGE_20", "strategy", Costs(2.5, 0.0, 0.1, 20), False, 1_000_000),
            ("EW_UNIVERSE_10", "ew", Costs(2.5, 0.0, 0.1, 10), False, 1_000_000),
            ("EW_UNIVERSE_20", "ew", Costs(2.5, 0.0, 0.1, 20), False, 1_000_000)]
    for name, kind, costs, lots, cap in spec:
        runs[name] = ctx.run(kind, costs, lots, cap, name)
        print("ran", name, round(time.time() - t0), "s", flush=True)

    rf_all = ctx.rf[ctx.start:ctx.end + 1]
    sess = panel.sessions[ctx.start:ctx.end + 1]
    # benchmarks (price indices, buy and hold)
    bench = {}
    for code, nm in [("000852", "CSI1000"), ("000905", "CSI500")]:
        bench[nm] = index_nav_frame(load_index_close(code), sess)
    rf_by_date = dict(zip(sess, rf_all))

    rows, cy_rows, ret_store = [], [], {}
    all_runs = {k: (r.nav, r.counters) for k, r in runs.items()}
    for nm, nv in bench.items():
        all_runs[nm] = (nv.assign(cash=0.0, n_hold=0), {})
    for name, (nav, cnt) in all_runs.items():
        rf = np.array([rf_by_date[d] for d in nav["date"]])
        for per, (a, b) in PERIODS.items():
            if nav["date"].iloc[0] > b:
                continue
            start = max(a, nav["date"].iloc[0])
            base = None
            if nav["date"].iloc[0] > a:
                base = float(nav["nav"].iloc[0])
                start = nav["date"].iloc[1]
            m, ret, cy = period_metrics(nav, rf, start, b, base_nav=base)
            ret_store[(name, per)] = (nav["date"][(nav["date"] >= start) & (nav["date"] <= b)].tolist(), ret, rf[(nav["date"] >= start).to_numpy() & (nav["date"] <= b).to_numpy()])
            row = {"run": name, "period": per, **m}
            if per == "DEV" and cnt:
                row.update({"delisted_holdings": cnt["delisted_holdings"], "lot_zero_orders": cnt["lot_zero"],
                            "t1_blocked": cnt["t1_blocked"], "signals": cnt["signals"],
                            "blocked_buy": cnt["blocked_buy"], "blocked_sell": cnt["blocked_sell"]})
            rows.append(row)
            if per == "DEV":
                for y, v in cy.items():
                    cy_rows.append({"run": name, "year": int(y), "ret": float(v)})
    # strategy over the CSI1000 overlap
    c1000_start = bench["CSI1000"]["date"].iloc[1]
    extra = []
    for name in ["STRAT_SMALL_NOMIN_10", "STRAT_SMALL_NOMIN_20", "EW_UNIVERSE_10", "EW_UNIVERSE_20"]:
        nav = runs[name].nav
        rf = np.array([rf_by_date[d] for d in nav["date"]])
        m, ret, cy = period_metrics(nav, rf, c1000_start, DEV_END)
        extra.append({"run": name, "period": "DEV_OVERLAP_CSI1000", **m})
    nv = bench["CSI1000"]
    rf = np.array([rf_by_date[d] for d in nv["date"]])
    m, _, _ = period_metrics(nv.assign(cash=0.0, n_hold=0), rf, c1000_start, DEV_END, base_nav=float(nv["nav"].iloc[0]))
    extra.append({"run": "CSI1000", "period": "DEV_OVERLAP_CSI1000", **m})
    rows += extra
    metrics = pd.DataFrame(rows)
    metrics.to_csv(OUT / "metrics.csv", index=False)
    pd.DataFrame(cy_rows).pivot(index="year", columns="run", values="ret").to_csv(OUT / "calendar_year_returns.csv")

    # trades / holdings / universe of the decision version
    main_run = runs["STRAT_SMALL_NOMIN_10"]
    pd.DataFrame(main_run.trades).to_csv(OUT / "trades_STRAT_SMALL_NOMIN_10.csv", index=False)
    pd.DataFrame(ctx.reb_log["STRAT_SMALL_NOMIN_10"]).to_csv(OUT / "holdings_per_rebalance_STRAT_SMALL_NOMIN_10.csv", index=False)
    pd.DataFrame(ctx.reb_log["STRAT_LARGE_10"]).to_csv(OUT / "holdings_per_rebalance_STRAT_LARGE_10.csv", index=False)
    uni_rows = []
    for m_, mem in ctx.univs.items():
        if panel.sessions[m_] >= "2009-06-01":
            elig = int(((panel.bars_before[m_] >= 250) & panel.has_bar[m_] & ~panel.st[m_]).sum())
            uni_rows.append({"month_end": panel.sessions[m_], "universe_size": int(mem.sum()), "eligible_before_rank": elig,
                             "inferred_st_flagged": int((panel.st[m_] & panel.has_bar[m_]).sum()),
                             "stocks_with_bar": int(panel.has_bar[m_].sum())})
    pd.DataFrame(uni_rows).to_csv(OUT / "universe_size.csv", index=False)
    runs["STRAT_SMALL_NOMIN_10"].nav.to_csv(OUT / "nav_STRAT_SMALL_NOMIN_10.csv", index=False)

    # bootstrap of excess-Sharpe differences (stationary block bootstrap, block 21, 2000 resamples, seed 20261019)
    boot = []
    idx_cache = {}

    def series_of(name, per):
        if per == "OV":                                  # CSI1000 only exists from 2014-10-17
            d, r, f = ret_store[(name, "DEV")]
            keep = [k for k, x in enumerate(d) if x >= c1000_start]
            return [d[k] for k in keep], r[keep], f[keep]
        return ret_store[(name, per)]

    def boot_diff(label, a_name, b_name, per):
        da, ra, _ = series_of(a_name, per)
        db, rb, _ = series_of(b_name, per)
        common = sorted(set(da) & set(db))
        ia = {d: k for k, d in enumerate(da)}
        ib = {d: k for k, d in enumerate(db)}
        xa = np.array([ra[ia[d]] - rf_by_date[d] for d in common])
        xb = np.array([rb[ib[d]] - rf_by_date[d] for d in common])
        t = len(common)
        if t not in idx_cache:
            idx_cache[t] = stats.stationary_bootstrap_indices(t, BOOT_BLOCK, BOOT_N, BOOT_SEED)
        pt, lo, hi = stats.bootstrap_sharpe_diff(xa, xb, idx_cache[t], 0.90)
        boot.append({"comparison": label, "period": per if per != "OV" else "DEV_OVERLAP_CSI1000", "n_days": t,
                     "xsharpe_diff": pt, "ci90_lo": lo, "ci90_hi": hi, "ci_excludes_0": bool(lo > 0 or hi < 0)})

    for per in PERIODS:
        boot_diff("STRAT_SMALL_NOMIN_10 - EW_UNIVERSE_10", "STRAT_SMALL_NOMIN_10", "EW_UNIVERSE_10", per)
        boot_diff("STRAT_SMALL_NOMIN_20 - EW_UNIVERSE_20", "STRAT_SMALL_NOMIN_20", "EW_UNIVERSE_20", per)
        boot_diff("STRAT_LARGE_10 - EW_UNIVERSE_10", "STRAT_LARGE_10", "EW_UNIVERSE_10", per)
        boot_diff("STRAT_SMALL_NOMIN_10 - CSI500", "STRAT_SMALL_NOMIN_10", "CSI500", per)
    boot_diff("STRAT_SMALL_NOMIN_10 - CSI1000", "STRAT_SMALL_NOMIN_10", "CSI1000", "OV")
    boot_diff("STRAT_SMALL_NOMIN_20 - CSI1000", "STRAT_SMALL_NOMIN_20", "CSI1000", "OV")
    boot_diff("EW_UNIVERSE_10 - CSI1000", "EW_UNIVERSE_10", "CSI1000", "OV")
    pd.DataFrame(boot).to_csv(OUT / "bootstrap.csv", index=False)

    # PSR(0) of the strategy excess returns
    psr_rows = []
    for nm in ["STRAT_SMALL_NOMIN_10", "STRAT_SMALL_NOMIN_20", "STRAT_SMALL_MIN5_10", "STRAT_LARGE_10", "EW_UNIVERSE_10"]:
        for per in PERIODS:
            d, r, f = ret_store[(nm, per)]
            psr_rows.append({"run": nm, "period": per, "psr0": stats.psr(r - f, 0.0)})
    pd.DataFrame(psr_rows).to_csv(OUT / "psr.csv", index=False)

    # decision
    def mrow(run, per):
        return metrics[(metrics["run"] == run) & (metrics["period"] == per)].iloc[0]
    dm = {"x_strat_10": mrow("STRAT_SMALL_NOMIN_10", "DEV")["xsharpe"], "x_ew_10": mrow("EW_UNIVERSE_10", "DEV")["xsharpe"],
          "x_strat_20": mrow("STRAT_SMALL_NOMIN_20", "DEV")["xsharpe"], "x_ew_20": mrow("EW_UNIVERSE_20", "DEV")["xsharpe"],
          "x_csi1000_overlap": mrow("CSI1000", "DEV_OVERLAP_CSI1000")["xsharpe"],
          "x_strat_csi1000_overlap": mrow("STRAT_SMALL_NOMIN_10", "DEV_OVERLAP_CSI1000")["xsharpe"],
          "x_strat20_csi1000_overlap": mrow("STRAT_SMALL_NOMIN_20", "DEV_OVERLAP_CSI1000")["xsharpe"],
          "xA_strat": mrow("STRAT_SMALL_NOMIN_10", "DEV_A")["xsharpe"], "xA_ew": mrow("EW_UNIVERSE_10", "DEV_A")["xsharpe"],
          "xB_strat": mrow("STRAT_SMALL_NOMIN_10", "DEV_B")["xsharpe"], "xB_ew": mrow("EW_UNIVERSE_10", "DEV_B")["xsharpe"],
          "mdd_strat": mrow("STRAT_SMALL_NOMIN_10", "DEV")["max_drawdown"], "mdd_ew": mrow("EW_UNIVERSE_10", "DEV")["max_drawdown"]}
    verdict = decide(dm)
    verdict["inputs"] = {k: float(v) for k, v in dm.items()}
    verdict["dev_mdd_within_40pct"] = bool(dm["mdd_strat"] <= 0.40)
    verdict["diagnostics"] = {k: (float(v) if isinstance(v, (int, float, np.floating)) else v) for k, v in diag.items()}
    (OUT / "verdict.json").write_text(json.dumps(verdict, indent=1, ensure_ascii=False, default=str))
    print(json.dumps(verdict, indent=1, ensure_ascii=False, default=str))
    print("done", round(time.time() - t0), "s")


if __name__ == "__main__":
    main()
