"""Orchestrates Part A + Part B, writes data_check.md, robustness.md, CSVs and VALIDATION_SUMMARY.md."""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from .. import runner as R
from ..data import verify_manifest
from . import data_check as DC
from . import robustness as RB
from .fetch import verify_manifest as verify_val_manifest
from .protocol_val import load_validation_protocol
from .world import build_world

OUT = Path(__file__).resolve().parent


def _p(x, d=2):
    return "n/a" if x is None or x != x else f"{x * 100:.{d}f}%"


def write_robustness_md(out_dir: Path, results: list[dict], a_failed: bool) -> None:
    L = ["# Part B: robustness (protocol_cn_validation.json)", "",
         "Descriptive only: nothing is selected or changed from these runs. Common setup: baseline engine, CN universe, "
         "amendment-1 suspension handling, SHIBOR cash, primary cost 5+5 bps, FULL = baseline scoring window "
         "(score_start 2014-09-01 for every run, so all cells share one sample), benchmarks CSI300_BH / CN_60_40. "
         "Per-cell check (B1): FULL xSharpe > CN_60_40 FULL xSharpe AND FULL MDD < CSI300_BH FULL MDD.", ""]
    for res in results:
        tag = res["tag"]
        base = res["baseline"]["CAP_DEF_M"]["FULL"]
        bc = res["bench"]
        L += [f"# Dataset: {tag}", "", f"Baseline reference (CAP_DEF_M FULL): CAGR {_p(base['cagr'])}, xSharpe {base['excess_sharpe']:.3f}, "
              f"MDD {_p(base['max_drawdown'])}; CN_60_40 FULL xSharpe {bc['CN_60_40']['FULL']['excess_sharpe']:.3f}; "
              f"CSI300_BH FULL MDD {_p(bc['CSI300_BH']['FULL']['max_drawdown'])}.", ""]
        b1 = res["b1"]
        L += ["## B1: parameter neighbourhood (momentum months x trend weeks)", "",
              "FULL excess Sharpe / MDD per cell (pass = per-cell check):", "",
              "| momentum \\ trend | " + " | ".join(f"{t}w" for t in sorted(b1.trend_weeks.unique())) + " |",
              "|---|" + "---|" * b1.trend_weeks.nunique()]
        for m in sorted(b1.momentum_months.unique()):
            cells = []
            for t in sorted(b1.trend_weeks.unique()):
                r = b1[(b1.momentum_months == m) & (b1.trend_weeks == t)].iloc[0]
                cells.append(f"{r.xsharpe:.3f} / {_p(r.mdd)} {'PASS' if r.chk_pass else 'FAIL'}")
            L.append(f"| {m}m | " + " | ".join(cells) + " |")
        n1 = int(b1.chk_pass.sum())
        L += ["", f"Cells passing: **{n1} of 9** (holds_if >= 7): **{'PASS' if n1 >= 7 else 'FAIL'}**. The (12m, 40w) cell is the "
              f"frozen candidate and reproduces the baseline (xSharpe {b1[(b1.momentum_months == 12) & (b1.trend_weeks == 40)].xsharpe.iloc[0]:.4f}).", "",
              "| momentum | trend | CAGR | xSharpe | MDD | xSharpe SUB_A | xSharpe SUB_B | pass |", "|---|---|---|---|---|---|---|---|"]
        for r in b1.itertuples():
            L.append(f"| {r.momentum_months} | {r.trend_weeks} | {_p(r.cagr)} | {r.xsharpe:.3f} | {_p(r.mdd)} | {r.xsharpe_sub_a:.3f} | "
                     f"{r.xsharpe_sub_b:.3f} | {'PASS' if r.chk_pass else 'FAIL'} |")
        b2 = res["b2"]
        L += ["", "## B2: execution lag (sessions after the normal execution session; strategy and both benchmarks lagged)", "",
              "| lag k | CAGR | xSharpe | MDD | bench CN_60_40 xSharpe | bench CSI300_BH MDD | pass | pass vs unlagged benchmarks |",
              "|---|---|---|---|---|---|---|---|"]
        for r in b2.itertuples():
            L.append(f"| {r.lag_sessions} | {_p(r.cagr)} | {r.xsharpe:.3f} | {_p(r.mdd)} | {r.bench_CN_60_40_xsharpe:.3f} | "
                     f"{_p(r.bench_CSI300_BH_mdd)} | {'PASS' if r.chk_pass else 'FAIL'} | {'PASS' if r.alt_pass_vs_unlagged_benchmarks else 'FAIL'} |")
        L += ["", f"All 5 offsets pass: **{'PASS' if b2.chk_pass.all() else 'FAIL'}**", ""]
        b3 = res["b3"]
        L += ["## B3: ETF substitution (Sina download + baseline adjustment path)", "",
              "| variant | status | CAGR | xSharpe | MDD | SUB_A xS | SUB_B xS | pass (baseline benchmarks) | pass (substituted-CSI300 benchmarks) | missing bars |",
              "|---|---|---|---|---|---|---|---|---|---|"]
        for r in b3.to_dict("records"):
            if r["run"] != "RUN":
                L.append(f"| {r['variant']} | NOT RUN | | | | | | | | |")
            else:
                L.append(f"| {r['variant']} | RUN | {_p(r['cagr'])} | {r['xsharpe']:.3f} | {_p(r['mdd'])} | {r['xsharpe_sub_a']:.3f} | "
                         f"{r['xsharpe_sub_b']:.3f} | {'PASS' if r['chk_pass'] else 'FAIL'} | "
                         f"{'PASS' if r['alt_pass_vs_substituted_benchmarks'] else 'FAIL'} | {r['missing_bars_in_data_range']} |")
        L += [""] + [f"- {n}" for n in res["b3_notes"]]
        b4 = res["b4"]
        L += ["", "## B4: small-account execution (100-share lots, max(5 CNY, 2.5 bp) commission + 5 bp slippage per side)", "",
              "| account | CAGR | xSharpe | MDD | mean cash weight | CAGR drop vs baseline (pp) | fees (CNY) | pass (same-account benchmarks) |",
              "|---|---|---|---|---|---|---|---|"]
        for r in b4.to_dict("records"):
            pc = r.get("chk_pass")
            L.append(f"| {r['account']} | {_p(r['cagr'])} | {r['xsharpe']:.3f} | {_p(r['mdd'])} | {_p(r['mean_cash_weight'])} | "
                     f"{r['cagr_drop_pp']:+.2f} | {r['total_fees']:.0f} | {'' if pc != pc or pc is None else ('PASS' if pc else 'FAIL')} |")
        L += ["", "The 'SENSITIVITY' rows are not in the protocol; they use 5 bp commission so that only rounding and the minimum fee "
              "differ from the baseline cost level. 20000 CNY is reported only (no threshold).", ""]
        b5 = res["b5"]
        ep = b5["episode"]
        L += ["## B5: max-drawdown decomposition (CAP_DEF_M, FULL, primary cost)", "",
              f"- Max drawdown {_p(ep['mdd'])}: peak **{ep['peak_date']}** (NAV {ep['peak_nav']:.0f}), trough **{ep['trough_date']}** "
              f"(NAV {ep['trough_nav']:.0f}), recovery **{ep['recovery_date']}** ({ep['sessions_peak_to_trough']} sessions peak to trough, "
              f"{ep['sessions_trough_to_recovery']} sessions trough to recovery).",
              f"- Held at the peak: {', '.join(b5['held_at_peak'] or [])}.",
              f"- Reconstruction check: daily P&L by holding vs NAV change, max abs error {b5['recon_max_abs_error']:.2e} CNY.", "",
              "Peak-to-trough contribution by holding (percentage points of peak NAV):", "",
              "| " + " | ".join(b5["peak_to_trough_contrib"].keys()) + " |", "|" + "---|" * len(b5["peak_to_trough_contrib"]),
              "| " + " | ".join(f"{v:+.2f}" for v in b5["peak_to_trough_contrib"].values()) + " |", "",
              "Weekly trend filter of the names held at the peak (first weekly fail after the peak) and the monthly signal that could act on it:", "",
              "| asset | first weekly trend FAIL after peak | next monthly signal | executed |", "|---|---|---|---|"]
        for s, v in b5["first_weekly_fail"].items():
            L.append(f"| {s} | {v['first_weekly_trend_fail_after_peak']} | {v['next_monthly_signal']} | {v['next_monthly_execution']} |")
        L += ["", "Month by month from the peak to recovery (contributions in percentage points of peak NAV; 'filter' = trend filter "
               "of each held asset at the monthly signal row, with the weekly close vs 40-week SMA gap; 511010 held through a DEF slot "
               "is not trend-tested):", ""]
        m = b5["months"]
        U = ["510300", "510050", "510500", "159915", "518880", "511010"]
        hdr = ["month", "ret", "entering holdings", "signal / feature week / exec", "target weights", "trend filter at signal"] + U + ["cash int.", "fees"]
        L += ["| " + " | ".join(hdr) + " |", "|" + "---|" * len(hdr)]
        for r in m.to_dict("records"):
            flag = "*" if r["contains_trough"] else ("" if r["in_peak_to_trough"] else "")
            L.append("| " + " | ".join([r["month"] + flag, f"{r['month_return'] * 100:+.1f}%", r["holdings_entering_month"],
                                         f"{r['signal_session']} / {r['feature_week_session']} / {r['execution_session']}",
                                         r["target_weights"], r["trend_filter_at_signal"]] +
                                        [f"{r[f'contrib_{s}_pct_of_peak']:+.2f}" for s in U] +
                                        [f"{r['contrib_cash_interest_pct_of_peak']:+.2f}", f"{r['contrib_fees_pct_of_peak']:+.2f}"]) + " |")
        L += ["", "(* = month contains the trough.)", ""]
        v = res["verdict"]
        L += ["## Verdicts (" + tag + ")", "", f"- B1: {'PASS' if v['B1']['holds'] else 'FAIL'} ({v['B1']['cells_passed']}/9)",
              f"- B2: {'PASS' if v['B2']['holds'] else 'FAIL'}", f"- B3: {v['B3']['holds'] if isinstance(v['B3']['holds'], str) else ('PASS' if v['B3']['holds'] else 'FAIL')}",
              f"- B4: {'PASS' if v['B4']['holds'] else 'FAIL'}", "- B5: descriptive (no threshold)", ""]
    (Path(out_dir) / "robustness.md").write_text("\n".join(L) + "\n")


def write_summary(out_dir: Path, A: dict, results: list[dict], proto: dict, deviations: list[str]) -> None:
    res = results[0]
    v = res["verdict"]
    ps = lambda b: "PASS" if b else "FAIL"
    L = ["# CN validation summary (protocol_cn_validation.json, sha256 verified at run time)", "",
         "| check | result | threshold / note |", "|---|---|---|",
         f"| A1 price adjustment | {A['verdicts']['A1']} | per symbol abs(ann diff) <= 0.10 pp and no unexplained >50 bp day; EM_HFQ unreachable |",
         f"| A2 NAV tracking | {A['verdicts']['A2']} | abs(ann diff) <= 0.50 pp per symbol |",
         f"| A3 corporate actions | {A['verdicts']['A3']} | Sina hfq.js vs Eastmoney dividend/split records |",
         f"| A4 suspensions | {A['verdicts']['A4']} | EM unadjusted bars unreachable; Tencent/Sina/EM-NAV status reported |",
         f"| Data-corrected rerun | {'TRIGGERED' if A['a_failed'] else 'NOT TRIGGERED'} | triggered only if A1 or A2 fails |",
         f"| B1 neighbourhood | {ps(v['B1']['holds'])} ({v['B1']['cells_passed']}/9) | >= 7 of 9 |",
         f"| B2 execution lag | {ps(v['B2']['holds'])} | all 5 offsets |",
         f"| B3 ETF substitution | {v['B3']['holds'] if isinstance(v['B3']['holds'], str) else ps(v['B3']['holds'])} | every runnable variant |",
         f"| B4 small account (100k) | {ps(v['B4']['holds'])} | CAGR drop <= 1.0 pp and per-cell check |",
         "| B5 2015 drawdown | descriptive | no threshold |", "", "## Deviations", ""] + [f"- {d}" for d in deviations] + [""]
    (Path(out_dir) / "VALIDATION_SUMMARY.md").write_text("\n".join(L) + "\n")


def run_all(out_dir: Path = OUT, log=print) -> dict:
    proto = load_validation_protocol()            # raises ProtocolChangedError on hash mismatch
    out_dir = Path(out_dir)
    verify_manifest()                              # baseline snapshot hashes
    verify_val_manifest()                          # validation snapshot hashes
    A = DC.run_part_a(out_dir)
    log("Part A verdicts: " + json.dumps(A["verdicts"]))
    results = []
    world = build_world()
    results.append(RB.run_part_b(world, proto, out_dir, "baseline", log))
    if A["a_failed"]:
        log("Part A failed: a data-corrected rerun is required (not implemented automatically); see data_check.md")
    write_robustness_md(out_dir, results, A["a_failed"])
    deviations = [
        "Eastmoney bar endpoints (ak.fund_etf_hist_em hfq and unadjusted, push2his.eastmoney.com) were unreachable (3 tries each, all 6 symbols, "
        "logged in validation/data/fetch_log.json). A1 as written and the EM part of A4 are therefore NOT RUN. Closest faithful versions: "
        "A1 against an independent reconstruction (Tencent unadjusted bars + Eastmoney distribution/split records through the baseline "
        "adjustment code); A4 against Tencent bars, Sina bars and Eastmoney NAV status. The A1 reference shares the adjustment code with the "
        "baseline, so it tests prices and event records, not the algorithm; A2 (NAV total return) is independent of it.",
        "A3 uses the Eastmoney distribution/split tables behind ak.fund_fh_em / ak.fund_cf_em (queried directly with the same URL/params, JSON-parsed instead of eval).",
        "A2 NAV total return is compounded from Eastmoney's daily growth rate, which is rounded to 0.01%.",
        "All Part B cells use the baseline score_start (2014-09-01) so every cell shares one sample; benchmarks for B2 are lagged by the same k "
        "(unlagged-benchmark outcome also reported); B3 passes against the baseline benchmarks (substituted-CSI300 benchmark outcome also reported); "
        "B4 passes against benchmarks run under the same account rules (fractional-benchmark outcome also reported).",
        "B4 commission schedule is exactly as written (2.5 bp, min 5 CNY, plus 5 bp slippage), which is cheaper than the baseline 5+5 bps for large trades; "
        "a non-protocol sensitivity with 5 bp commission is shown.",
        "B4 lot executor: targets floor(w * V_pre / (open * 100)) lots on the pre-trade value; if fees would make cash negative, lots are removed from the "
        "largest buy one at a time. Every non-zero lot change is a trade (minimum fee applies).",
    ]
    write_summary(out_dir, A, results, proto, deviations)
    return {"A": A, "B": results}
