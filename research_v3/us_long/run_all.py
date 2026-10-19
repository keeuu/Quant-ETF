"""Run everything and write artifacts/summary.md:  python -m research_v3.us_long.run_all"""
from __future__ import annotations

import json

import pandas as pd

from .run_primary import ART, run_primary
from .run_secondary import run_all as run_secondary_all
from .run_sensitivity import run_ffill_signals

KEY_YEARS = [1973, 1974, 1987, 2000, 2001, 2002]


def p(x, d=2):
    return f"{x * 100:.{d}f}%" if x == x and x is not None else "n/a"


def metric_table(tbl, part, order):
    sub = tbl[tbl.partition == part].set_index("strategy").loc[order]
    L = [f"### {part} ({sub['start'].iloc[0]} to {sub['end'].iloc[0]})", "",
         "| strategy | CAGR | vol | xSharpe | MDD | MDD peak..trough | worst yr | turnover/yr | cash wt | bond wt |",
         "|---|---|---|---|---|---|---|---|---|---|"]
    for s, r in sub.iterrows():
        wy = f"{p(r.worst_year)} ({r.worst_year_which})" if r.worst_year == r.worst_year else "n/a"
        L.append(f"| {s} | {p(r.cagr)} | {p(r.ann_vol)} | {r.excess_sharpe:.3f} | {p(r.max_drawdown)} | "
                 f"{r.mdd_peak}..{r.mdd_trough} | {wy} | {r.ann_one_way_turnover:.2f} | {p(r.mean_cash_weight)} | "
                 f"{p(r.mean_bond_weight)} |")
    return L + [""]


def sec_table(tbl, order):
    L = []
    for part in ("FULL", "SUB_A", "SUB_B"):
        sub = tbl[tbl.partition == part].set_index("strategy").loc[order]
        L += [f"**{part}** ({sub['start'].iloc[0]} to {sub['end'].iloc[0]})", "",
              "| strategy | CAGR | vol | xSharpe | MDD | worst yr | turnover/yr |", "|---|---|---|---|---|---|---|"]
        for s, r in sub.iterrows():
            L.append(f"| {s} | {p(r.cagr)} | {p(r.ann_vol)} | {r.excess_sharpe:.3f} | {p(r.max_drawdown)} | "
                     f"{p(r.worst_year)} | {r.ann_one_way_turnover:.2f} |")
        L.append("")
    return L


def boot_lines(bt):
    L = ["| partition | strategy | reference | diff | CI90 lo | CI90 hi | includes 0 |", "|---|---|---|---|---|---|---|"]
    for r in bt.itertuples():
        L.append(f"| {r.partition} | {r.strategy} | {r.reference} | {r.xsharpe_diff:+.3f} | {r.ci90_lo:+.3f} | "
                 f"{r.ci90_hi:+.3f} | {r.ci_includes_0} |")
    return L


def main():
    res = run_primary(log=print)
    sec = run_secondary_all()
    sens_tbl, sens_v, sens_vc, sens_ss, sens_n = run_ffill_signals()
    tbl, cy, bt, rep = res["tbl"], res["cy"], res["bt"], res["report"]
    v, vc = res["verdict"], res["verdict_ch"]
    order = ["CAP_DEF_M", "BASE_M", "SPY_BH", "US_60_40", "SPY_SMA10"]
    L = ["# us_long: out-of-period test of frozen CAP_DEF_M (1972-2005 proxies) and CRASH_WEEKLY_EXIT challenger", "",
         f"Protocols (hash-verified at run time): protocol_us_long.json `{rep['protocol_sha256']['main'][:8]}...`, "
         f"protocol_challenger_crash.json `{rep['protocol_sha256']['crash'][:8]}...`. Costs 5+5 bps, DTB3 cash, "
         "signal at close t, trade at close t+1.", "",
         "## Outcomes", "",
         f"- **Frozen CAP_DEF_M (protocol_us_long): {v['outcome']}**",
         f"  - FULL xSharpe > US_60_40: {v['C1_full_xsharpe_gt_60_40']}",
         f"  - FULL MDD < SPY_BH MDD: {v['C2_full_mdd_lt_spy_bh']}",
         f"  - SUB_A xSharpe > US_60_40: {v['C3a_sub_a_xsharpe_gt_60_40']}; SUB_B: {v['C3b_sub_b_xsharpe_gt_60_40']}",
         f"- **CRASH_WEEKLY_EXIT (protocol_challenger_crash): {vc['outcome']}**",
         f"  - FULL MDD lower by >= 3.0 pp: {vc['C1_full_mdd_lower_by_ge_3pp']} (reduction {vc['full_mdd_reduction_pp']:.2f} pp)",
         f"  - FULL xSharpe not lower by more than 0.05: {vc['C2_full_xsharpe_not_lower_by_gt_0.05']} (change {vc['full_xsharpe_change']:+.3f})",
         f"  - MDD not higher than frozen in SUB_A: {vc['C3a_sub_a_mdd_not_higher']}; in SUB_B: {vc['C3b_sub_b_mdd_not_higher']}", "",
         "## Data", "",
         f"Sessions {rep['sessions'][0]}..{rep['sessions'][1]} ({rep['sessions'][2]}); first warm monthly signal "
         f"{rep['first_warm_signal']} -> score_start {rep['score_start']}; score_end {rep['score_end']}.", "",
         "| proxy | first date | forward-filled sessions (valuation) | weekly signal dates with no observation |",
         "|---|---|---|---|"]
    for s, fd in rep["first_date"].items():
        L.append(f"| {s} | {fd or 'unavailable'} | {rep['fills_valuation_sessions'][s]} | "
                 f"{rep['weekly_signal_dates_with_no_observation'][s]} |")
    L += ["", f"Frozen-rule executions at a forward-filled price: {rep['frozen_exec_on_filled_price_count']}. "
          f"Weekly events {rep['n_weekly_events']}, monthly events {rep['n_monthly_events']}.", "",
          "## Frozen rule, BASE (context only) and benchmarks (CRASH_WEEKLY_EXIT shown in the next section)", ""]
    for part in ("FULL", "SUB_A", "SUB_B"):
        L += metric_table(tbl, part, order)
    spy_idx = res["spy_idx_year"]
    cyp = cy.pivot(index="year", columns="strategy", values="return")
    L += ["## Calendar-year returns, key years", "",
          "Scoring starts 1972-03-01, so 1972 is a partial year; 1973 onward are full years. "
          "'SPY proxy (Jan-Dec)' is the proxy index over the whole calendar year.", "",
          "| year | CAP_DEF_M | SPY_BH | US_60_40 | CRASH_WEEKLY_EXIT | SPY proxy (Jan-Dec) |", "|---|---|---|---|---|---|"]
    for y in KEY_YEARS:
        L.append(f"| {y} | {p(cyp.loc[y, 'CAP_DEF_M'])} | {p(cyp.loc[y, 'SPY_BH'])} | {p(cyp.loc[y, 'US_60_40'])} | "
                 f"{p(cyp.loc[y, 'CRASH_WEEKLY_EXIT'])} | {p(spy_idx.get(y, float('nan')))} |")
    L += ["", "All years: calendar_year_returns.csv.", "", "## Bootstrap (90% CI of excess-Sharpe difference; block 21, 2000 resamples, seed 20261019)", ""]
    L += boot_lines(bt)
    inc = bt[bt.ci_includes_0]
    L += ["", "Rows whose CI includes 0 are not statistically distinguishable from zero: " +
          "; ".join(f"{r.partition} {r.strategy} vs {r.reference}" for r in inc.itertuples()) + ".", "",
          "## Challenger: CRASH_WEEKLY_EXIT vs frozen CAP_DEF_M (same data)", ""]
    chorder = ["CAP_DEF_M", "CRASH_WEEKLY_EXIT"]
    for part in ("FULL", "SUB_A", "SUB_B"):
        L += metric_table(tbl, part, chorder)
    fu = tbl[(tbl.partition == "FULL")].set_index("strategy")
    L += [f"Weekly exit events: {rep['n_weekly_exit_events']} (assets exited: {rep['n_assets_exited']}); "
          f"turnover/yr {fu.loc['CAP_DEF_M', 'ann_one_way_turnover']:.2f} -> {fu.loc['CRASH_WEEKLY_EXIT', 'ann_one_way_turnover']:.2f} "
          f"({fu.loc['CRASH_WEEKLY_EXIT', 'ann_one_way_turnover'] - fu.loc['CAP_DEF_M', 'ann_one_way_turnover']:+.2f}). "
          "Event list: crash_weekly_exits.csv.", ""]
    dcy = (cyp["CRASH_WEEKLY_EXIT"] - cyp["CAP_DEF_M"]) * 100
    L += ["Calendar-year difference (CRASH minus frozen, pp), years with |diff| >= 1 pp: " +
          ", ".join(f"{int(y)}: {d:+.1f}" for y, d in dcy.items() if abs(d) >= 1.0), ""]
    L += ["## Secondary, information-only challenger runs (cannot support the challenger)", ""]
    for name, title in (("us_2006_2020", "US 2006-02..2020-12 (research_v2 snapshot, real ETFs, next-open execution)"),
                        ("cn_2014_2026", "CN 2014-09..2026-09 (cn_transfer data, amendment-1 suspension rules)")):
        r = sec[name]
        o = ["CAP_DEF_M", "CRASH_WEEKLY_EXIT"] + [s for s in r["tbl"].strategy.unique() if s not in ("CAP_DEF_M", "CRASH_WEEKLY_EXIT")]
        L += [f"### {title}", "", r["note"], ""]
        L += sec_table(r["tbl"], o)
        vv = r["verdict"]
        L += [f"Criteria (information only): FULL MDD reduction {vv['full_mdd_reduction_pp']:.2f} pp (>=3: {vv['C1_full_mdd_lower_by_ge_3pp']}); "
              f"FULL xSharpe change {vv['full_xsharpe_change']:+.3f} (ok: {vv['C2_full_xsharpe_not_lower_by_gt_0.05']}); "
              f"MDD not higher SUB_A {vv['C3a_sub_a_mdd_not_higher']}, SUB_B {vv['C3b_sub_b_mdd_not_higher']} -> would be {vv['outcome']}. "
              f"Weekly exit events: {len(r['ch_log'])}.", ""]
        if name == "cn_2014_2026":
            L += [f"Frozen-rule NAV vs the committed cn_transfer artifact: max abs difference {r['ref_nav_maxabs_diff']:.2e}.", ""]
            pp = r["postponements"]
            L += [f"Postponed rebalances by strategy: {json.dumps({k: len(x) for k, x in pp.items()})}.", ""]
        L += ["Bootstrap (CRASH vs frozen):", ""] + boot_lines(r["bt"]) + [""]
    L += ["## Sensitivity (information only): signals see forward-filled values", "",
          "The primary run follows the protocol literally (a proxy without a new observation on a session shows NaN to the "
          "signals; the fill is used for valuation only). Here signals read the forward-filled series instead.", ""]
    for part in ("FULL", "SUB_A", "SUB_B"):
        L += metric_table(sens_tbl.assign(mean_bond_weight=sens_tbl["mean_bond_weight"]), part,
                          ["CAP_DEF_M", "CRASH_WEEKLY_EXIT", "SPY_BH", "US_60_40"])
    L += [f"Outcomes in this sensitivity: frozen {sens_v['outcome']}, challenger {sens_vc['outcome']}.", ""]
    L += ["## Deviations from the protocols and data notes", ""] + DEVIATIONS
    (ART / "summary.md").write_text("\n".join(L) + "\n")
    return res


DEVIATIONS = [
    "1. **EEM proxy unavailable.** The Ken French library has no daily Emerging Markets file (only monthly "
    "'Emerging_5_Factors', etc.). Per the protocol ('else unavailable') EEM is never in the universe; INTL_EQ = EFA proxy only, from 1990-07-02.",
    "2. **Gold source.** FRED no longer serves the London fix series (GOLDAMGBD228NLBM / GOLDPMGBD228NLBM return an error page); "
    "the LBMA JSON feed is behind a Cloudflare challenge and stooq requires a JavaScript challenge (not bypassed). Used: GoldPrice.com "
    "daily CSV `gold-price-history.csv` (CC BY 4.0; per the site LBMA fixes through about 2014/15, Metals.dev closes after; "
    "AM/PM fix not specified), first date 1968-04-01. Only 1971-2005 is used (LBMA fixes).",
    "3. **QQQ proxy is price-only** (^IXIC close via yfinance, first 1971-02-05), as declared in the protocol.",
    "4. **Execution lag.** Signals at close t, trades at the close of the next session (declared). Implemented by an adapter engine "
    "(engine_ul.py) with exec price = valuation price of the execution session; with open execution on the research_v2 snapshot it "
    "reproduces run_backtest_v3 exactly (tests).",
    "5. **Missing observations.** Reading the protocol literally, signals see NaN where a proxy has no observation on a session (gold London "
    "holidays, bond-market holidays), valuation uses the forward-filled level; fills are counted above. A forward-filled-signal sensitivity "
    "is reported and does not change either outcome.",
    "6. **Synthetic bonds** use consecutive observed yield dates with accrual y_{t-1}/252 per observation (no roll-down); on bond-market "
    "holidays the index is carried forward. FRED's DGS30 currently contains values for 2002-02..2006-02 (the period when Treasury "
    "discontinued the 30-year); they were used as published, no gap handling was needed. TLT proxy starts 1977-02-15.",
    "7. **Index base.** Return-based proxies start at 100 on their first session; ^IXIC and gold use their own levels.",
    "8. **score_start** (not defined precisely): first COMPLETE monthly row where every asset with an observation on or before the source "
    "weekly signal has finite 12M momentum and a computable 40-week SMA. Result 1972-03-01 (signal 1972-02-29). Assets that start later "
    "(TLT 1977, EFA 1990) enter the ranking only once their own 12M/40-week windows are full.",
    "9. **Partitions** SUB_A / SUB_B are slices of one continuous FULL run (as in the cn_transfer study), not separate restarts. "
    "SUB_B uses 1990-01-01..2005-12-30 (last session 2005-12-30; the Dec-30 monthly signal executes in 2006, outside the sample).",
    "10. **Benchmarks** reuse research_v3.step4.benchmark_frames on the matched monthly schedule (US_60_40 = 60% SPY / 40% IEF proxy; "
    "SPY_BH buys at the first monthly execution; SPY_SMA10 as in step 4).",
    "11. **CRASH_WEEKLY_EXIT implementation choices** (rule text unchanged): a weekly signal that coincides with a monthly signal session is "
    "treated as monthly; 'currently held' = target holdings after the last executed full/exit row; an exit sells only the failing holding "
    "at the next execution (close t+1 in the primary run) and buys the DEF choice with the proceeds, costs 5+5 bps on gross traded notional "
    "(self-financing, same convention as the ledger); remaining positions are not rebalanced and drift until the next monthly signal. "
    "A trend failure caused by missing data (no SMA) counts as a failure, as in the frozen test.",
    "12. **Secondary US 2006-2020** is one continuous run from 2006-02-01 (not the separate train / validation runs of earlier steps), real "
    "adj_open execution; SUB_A = train period, SUB_B = validation period. **Secondary CN** uses the cn_transfer data, calendar and amendment-1 "
    "suspension rules; an exit postponed by a suspension waits in a queue and is superseded only by the next monthly row.",
    "13. Bootstrap CIs use the research_v3 stationary bootstrap with the protocol seed; the challenger CI is vs the frozen rule.",
]


if __name__ == "__main__":
    main()
