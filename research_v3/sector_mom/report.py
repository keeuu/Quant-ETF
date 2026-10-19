"""summary.md writer for the development run."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

ORDER = ["S1_WEEKLY_MOM", "S2_MA10_EXIT_WEEKLY_REENTRY", "S3_MA5_EXIT_WEEKLY_REENTRY", "S4_MA10_EXIT_DAILY_REENTRY",
         "CSI300_BH", "EW_SECTOR"]


def pc(x, d=2):
    return f"{x * 100:.{d}f}%" if x == x and x is not None else "n/a"


def f3(x):
    return f"{x:.3f}" if x == x and x is not None else "n/a"


def table(header, rows):
    return ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)] + ["| " + " | ".join(map(str, r)) + " |" for r in rows]


def write_summary(out_dir: Path, c: dict) -> None:
    M, BT, PD, sel, inc = c["M"], c["BT"], c["PD"], c["sel"], c["inc"]
    g = lambda cost, lot, s, per: M[(M.cost == cost) & (M.lot_mode == lot) & (M.strategy == s) & (M.period == per)].iloc[0]
    L = ["# sector_mom development phase (score start to 2024-09-30)", "",
         f"Protocol sha256 {c['protocol_sha']} (verified at run time). Holdout NOT loaded (guard: sessions after 2024-09-30 raise).", "",
         f"DEV: {c['start']} .. {c['end']}; DEV_A: {c['pers']['DEV_A'][0]} .. {c['pers']['DEV_A'][1]}; DEV_B: {c['pers']['DEV_B'][0]} .. {c['pers']['DEV_B'][1]}.", ""]
    cand = sel["candidate"]
    L += ["## Selection outcome", "",
          (f"**Candidate: {cand}**" if cand else "**No strategy qualifies.** Holdout is not run unless the user explicitly asks for information only."),
          "", f"Qualifiers: {sel['qualifiers']}", "",
          "Criteria (all required; primary cost 2.5+10 bp AND sensitivity 2.5+20 bp). Max drawdown is a positive depth, lower is better.", ""]
    rows = []
    for s in sel["detail"]:
        for cost in ("primary", "sens"):
            x = sel["detail"][s][cost]
            rows.append([s, cost, f"{f3(x['xs_DEV'])} vs EW {f3(x['xs_DEV_EW'])} / CSI {f3(x['xs_DEV_CSI'])}", x["C1_xs_gt_EW_and_CSI"],
                         f"{pc(x['mdd_DEV'])} vs CSI {pc(x['mdd_DEV_CSI'])}", x["C2_mdd_lt_CSI"],
                         f"A {f3(x['xs_A'])} vs {f3(x['xs_A_EW'])}; B {f3(x['xs_B'])} vs {f3(x['xs_B_EW'])}", x["C3_xs_gt_EW_in_A_and_B"], x["all"]])
        rows[-1].append(sel["detail"][s]["qualifies"])
        rows[-2].append("")
    L += table(["strategy", "cost", "C1 DEV xSharpe > EW & CSI300", "C1", "C2 DEV MDD < CSI300", "C2", "C3 xSharpe > EW in A and B", "C3", "cost-all", "qualifies (both costs)"], rows)
    L += ["", "## DEV main table (primary cost 2.5+10 bp, fractional shares, 1,000,000 CNY)", ""]
    hdr = ["strategy", "CAGR", "vol", "xSharpe", "MDD", "worst yr", "turnover/yr", "cost drag", "round trips", "fills", "win rate", "avg hold d", "mean cash", "lim-blk buy/sell"]
    rows = []
    for s in ORDER:
        r = g("primary", "fractional1m", s, "DEV")
        rows.append([s, pc(r.cagr), pc(r.ann_vol), f3(r.xsharpe), pc(r.max_drawdown), f"{pc(r.worst_year)} ({r.worst_year_label})", f"{r.ann_one_way_turnover:.2f}",
                     pc(r.cost_drag), int(r.n_round_trips), int(r.n_fills), pc(r.win_rate, 1), f"{r.avg_holding_days:.1f}", pc(r.mean_cash_weight),
                     f"{int(r.limit_blocked_buy)}/{int(r.limit_blocked_sell)}"])
    L += table(hdr, rows)
    L += ["", "MDD dates (DEV, primary): "] + [f"- {s}: peak {g('primary','fractional1m',s,'DEV').mdd_peak_date}, trough {g('primary','fractional1m',s,'DEV').mdd_trough_date}" for s in ORDER]
    L += ["", "## xSharpe by period (primary / sensitivity)", ""]
    rows = []
    for s in ORDER:
        rows.append([s] + [f"{f3(g('primary','fractional1m',s,p).xsharpe)} / {f3(g('sens','fractional1m',s,p).xsharpe)}" for p in ("DEV", "DEV_A", "DEV_B")])
    L += table(["strategy", "DEV", "DEV_A", "DEV_B"], rows)
    L += ["", "## Sensitivity cost (2.5+20 bp) DEV table", ""]
    rows = []
    for s in ORDER:
        r = g("sens", "fractional1m", s, "DEV")
        rows.append([s, pc(r.cagr), pc(r.ann_vol), f3(r.xsharpe), pc(r.max_drawdown), f"{r.ann_one_way_turnover:.2f}", pc(r.cost_drag), int(r.n_round_trips), pc(r.win_rate, 1), pc(r.mean_cash_weight)])
    L += table(["strategy", "CAGR", "vol", "xSharpe", "MDD", "turnover/yr", "cost drag", "round trips", "win rate", "mean cash"], rows)
    L += ["", "## Secondary: 100,000 CNY, 100-share lots, min 5 CNY commission (DEV)", ""]
    rows = []
    for s in ORDER:
        r = g("primary", "lots100k", s, "DEV")
        rs = g("sens", "lots100k", s, "DEV")
        rows.append([s, pc(r.cagr), pc(r.ann_vol), f3(r.xsharpe), pc(r.max_drawdown), f"{r.ann_one_way_turnover:.2f}", int(r.n_round_trips), pc(r.mean_cash_weight), f3(rs.xsharpe)])
    L += table(["strategy", "CAGR", "vol", "xSharpe", "MDD", "turnover/yr", "round trips", "mean cash", "xSharpe (sens cost)"], rows)
    L += ["", "## Calendar-year returns (primary, fractional; * = partial year)", ""]
    cy = c["cy"]
    cy = cy[(cy.cost == "primary") & (cy.lot_mode == "fractional1m")]
    pv = cy.pivot(index="year", columns="strategy", values="return")[ORDER]
    fy = cy[cy.strategy == ORDER[0]].set_index("year")["full_year"]
    L += table(["year"] + ORDER, [[f"{y}{'' if fy[y] else '*'}"] + [pc(r[s]) for s in ORDER] for y, r in pv.iterrows()])
    L += ["", "## Bootstrap 90% CI of excess-Sharpe difference (primary cost; stationary block 21, 2000 resamples, seed 20261019)", ""]
    rows = [[r.period, r.strategy, r.reference, f"{r.xsharpe_diff:+.3f}", f"{r.ci90_lo:+.3f}", f"{r.ci90_hi:+.3f}", "yes" if r.ci_includes_0 else "no"] for r in BT.itertuples()]
    L += table(["period", "strategy", "vs", "diff", "lo", "hi", "CI includes 0"], rows)
    L += ["", "## PSR (vs 0, excess returns) and DSR (N = 4), DEV", ""]
    rows = [[r.cost, r.strategy, f3(r.psr0_excess), f3(r.dsr_N4), f"{r.sr0_annual:.3f}"] for r in PD.itertuples()]
    L += table(["cost", "strategy", "PSR(0)", "DSR(N=4)", "SR0 annualised"], rows)
    uot = c["uot"]
    sc = uot[uot.execution_date >= c["start"]]
    L += ["", "## Universe", "", f"Included ETFs in classified list: {len(inc)}; with price data: {int(c['panel'].is_sector.sum())}; without data: {len(c['missing'])}.",
          f"Eligible count (after dedup) over scored signal dates: min {int(sc.eligible_post_dedup.min())}, median {int(sc.eligible_post_dedup.median())}, max {int(sc.eligible_post_dedup.max())}; "
          f"pre-dedup min {int(sc.eligible_pre_dedup.min())}, max {int(sc.eligible_pre_dedup.max())}. First signal with >= 15: {sc.signal_date.iloc[0]} (executes {c['start']}).", ""]
    d = c["drep"]
    L += ["", "## Data", "", f"- Source: THS forward-adjusted bars from 2021-10-19 (primary) extended backwards with akshare/Sina self-built adjusted bars; {d['source_by_symbol_kind']}.",
          f"- Included codes without any price data: {d['n_without_bars']} (not found in THS or Sina); included ETFs with bars inside DEV: {d['n_with_bars']}.",
          f"- Overlap check THS vs Sina (p50/p95/max): close-ratio deviation {d['overlap_ratio_max_dev_p50_p95_max']}; daily log-return difference {d['overlap_daily_logret_max_abs_diff_p50_p95_max']}; turnover ratio {d['overlap_turnover_median_ratio_p50_p95_max']}.",
          f"- Missing in-listing-span session bars: {d['missing_session_bars_inside_listing_span']['total']} across {d['missing_session_bars_inside_listing_span']['n_symbols']} ETFs (worst: {d['missing_session_bars_inside_listing_span']['worst'][:3]}).",
          "- Delisted ETFs: no source provides them; survivorship bias present (see deviations).", ""]
    dev = Path(out_dir) / "deviations.md"
    L += ["## Deviations and interpretations", "", dev.read_text() if dev.exists() else "(deviations.md missing)", ""]
    (Path(out_dir) / "summary.md").write_text("\n".join(L) + "\n")
