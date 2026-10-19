"""Step 4: benchmarks, multiple-testing adjustment (PSR/DSR), bootstrap vs 60/40, candidate selection.

Everything is driven by the hash-verified research_v3/protocol_step4.json (V3_DEFAULT settings, train/validation only).
"""
from __future__ import annotations

import bisect
import json
import subprocess
from pathlib import Path
from typing import Mapping, Sequence

import numpy as np
import pandas as pd

from research_v2.contracts import SignalFrame, UNIVERSE
from research_v2.signals import compute_matched_monthly_signals, compute_model_signals_for_events

from .cash_yield import CashYieldCurve, load_irx
from .config import V3_DEFAULT
from .engine import run_backtest_v3
from .protocol_step3 import load_verified as load_step3
from .protocol_step4 import load_verified
from .runner import ROOT, prepare_signals
from .stats import (annual_to_daily_var, bootstrap_sharpe_diff, dsr, expected_max_sr, psr,
                    stationary_bootstrap_indices)
from .step2 import _setup, excess_sharpe
from .step3 import MIN_YEAR_SESSIONS, calendar_year_returns, mean_def_weight, variant_frames

OUT_DIR = ROOT / "research_v3/artifacts/step4"
BENCH = ["SPY_BH", "60_40", "SPY_SMA10", "EW8"]
STRATS = [("BASE", "W"), ("BASE", "M"), ("DEF", "W"), ("DEF", "M"), ("CAP_DEF", "W"), ("CAP_DEF", "M"),
          ("CAND", "W"), ("CAND_CAP_DEF", "W")]
POST_HOC = {"CAND", "CAND_CAP_DEF"}
NUM = ["cagr", "ann_vol", "sharpe", "excess_sharpe", "max_drawdown", "worst_year", "year_2008",
       "ann_one_way_turnover", "mean_cash_weight", "psr0", "dsr"]


# ---------------------------------------------------------------- benchmark signals
def spy_sma_decision(dates: Sequence[str], closes: Sequence[float], signal_session: str, n: int = 10) -> str:
    """"SPY" if close(signal) > mean of the n latest month-end closes ending at signal, else "CASH".

    Month-end values: the last observation of each calendar month strictly before the month of signal_session,
    plus the close at signal_session itself. Only dates <= signal_session are read. Fewer than n values -> CASH."""
    k = bisect.bisect_right(list(dates), signal_session)
    d, c = list(dates[:k]), list(closes[:k])
    if not d or d[-1] != signal_session:
        return "CASH"
    sig_month = signal_session[:7]
    last_of_month: dict[str, float] = {}
    for di, ci in zip(d, c):
        if di[:7] < sig_month:
            last_of_month[di[:7]] = ci
    vals = [last_of_month[m] for m in sorted(last_of_month)] + [c[-1]]
    if len(vals) < n:
        return "CASH"
    sma = float(np.mean(vals[-n:]))
    return "SPY" if c[-1] > sma else "CASH"


def _weights(d: Mapping[str, float]) -> dict:
    w = {s: 0.0 for s in UNIVERSE}
    w["CASH"] = 0.0
    w.update(d)
    return w


def benchmark_frames(view, monthly_rows: Sequence[dict]) -> dict[str, SignalFrame]:
    """Benchmark SignalFrames on the v2 monthly execution schedule (rows inside the partition only)."""
    sched = [r for r in monthly_rows if r.get("status") == "COMPLETE"
             and view.score_start <= r["execution_session"] <= view.score_end]
    spy = view.bars["SPY"]
    dates = spy["Date"].astype(str).tolist()
    closes = spy["adj_close"].astype(float).tolist()

    def mk(model_id, wfun, rows=sched):
        out = [{"signal_session": r["signal_session"], "execution_session": r["execution_session"],
                "status": "COMPLETE", "weights": _weights(wfun(r))} for r in rows]
        return SignalFrame(model_id=model_id, rows=out, lineage=[])

    return {
        "SPY_BH": mk("SPY_BH", lambda r: {"SPY": 1.0}, sched[:1]),
        "60_40": mk("60_40", lambda r: {"SPY": 0.6, "IEF": 0.4}),
        "SPY_SMA10": mk("SPY_SMA10", lambda r: ({"SPY": 1.0} if spy_sma_decision(dates, closes, r["signal_session"]) == "SPY"
                                               else {"CASH": 1.0})),
        "EW8": mk("EW8", lambda r: {s: 1.0 / len(UNIVERSE) for s in UNIVERSE}),
    }


# ---------------------------------------------------------------- helpers
def daily_excess(nav: pd.DataFrame, curve: CashYieldCurve) -> np.ndarray:
    d = nav["date"].tolist()
    rf = np.array([0.0] + [curve.accrual_factor(d[i - 1], d[i]) - 1.0 for i in range(1, len(d))])
    return nav["daily_return"].to_numpy(dtype=float) - rf


def _metrics(nav, m, curve, frame=None) -> dict:
    yr = calendar_year_returns(nav, V3_DEFAULT.initial_nav)
    full = yr[yr["sessions"] >= MIN_YEAR_SESSIONS]["ret"]
    return {
        "cagr": m["cagr"], "ann_vol": m["annualized_volatility"],
        "sharpe": m["sharpe_ratio"] if m["sharpe_ratio"] is not None else float("nan"),
        "excess_sharpe": excess_sharpe(nav, curve), "max_drawdown": m["max_drawdown"],
        "worst_year": float(full.min()) if len(full) else float("nan"),
        "year_2008": float(yr.loc[2008, "ret"]) if 2008 in yr.index and yr.loc[2008, "sessions"] >= MIN_YEAR_SESSIONS else float("nan"),
        "ann_one_way_turnover": m["annualized_one_way_turnover"], "mean_cash_weight": m["mean_cash_weight"],
    }


def trial_count(art_dir: Path | None = None) -> tuple[int, dict]:
    art = art_dir or ROOT / "research_v3/artifacts"
    s1 = pd.read_csv(art / "step1/comparison_long.csv")
    s2 = pd.read_csv(art / "step2/step2_long.csv")
    s3 = pd.read_csv(art / "step3/step3_long.csv")
    n1 = s1[s1.setting == "v3_new"]["model_id"].nunique()
    k2 = s2[s2.variant != "BASE"][["variant", "cadence", "trend_weeks"]].drop_duplicates()
    k3 = s3[s3.variant != "BASE"][["variant", "cadence", "trend_weeks"]].drop_duplicates()
    info = {"step1_models": int(n1), "step2_non_base": int(len(k2)), "step3_non_base": int(len(k3)),
            "step2_variants": sorted(k2.variant.unique().tolist()), "step3_variants": sorted(k3.variant.unique().tolist()),
            "overlap_step2_step3": int(len(k2.merge(k3))), "v2_original_models": 18,
            "note": "BASE of steps 2/3 == step-1 momentum-12 models (counted once, in step 1)"}
    return int(n1 + len(k2) + len(k3)), info


def config_pool(part: str, view_frames, curve, art_dir: Path | None = None) -> dict:
    """excess Sharpe (annualised) of every distinct evaluated configuration in a partition."""
    art = art_dir or ROOT / "research_v3/artifacts"
    view, frames = view_frames
    pool = {}
    for mid, sf in frames.items():
        nav, _r, _m = run_backtest_v3(view, sf, V3_DEFAULT, curve)
        pool[("S1", mid)] = excess_sharpe(nav, curve)
    s2 = pd.read_csv(art / "step2/step2_long.csv")
    s3 = pd.read_csv(art / "step3/step3_long.csv")
    # identity check: step-1 M12 models == BASE rows of step 2
    for _, r in s2[(s2.variant == "BASE") & (s2.partition == part)].iterrows():
        mid = f"{r.cadence}_M12_T{int(r.trend_weeks)}"
        if abs(pool[("S1", mid)] - r.excess_sharpe) > 1e-9:
            raise AssertionError(f"BASE != step1 {mid}: {pool[('S1', mid)]} vs {r.excess_sharpe}")
    for tag, df in (("S2", s2), ("S3", s3)):
        sub = df[(df.variant != "BASE") & (df.partition == part)]
        for _, r in sub.iterrows():
            pool[(tag, r.variant, r.cadence, int(r.trend_weeks))] = float(r.excess_sharpe)
    return pool


# ---------------------------------------------------------------- main run
def run_step4(art_dir: Path | None = None):
    proto = load_verified()  # refuses to run if changed
    p3 = load_step3()
    curve = CashYieldCurve(load_irx())
    n_trials, tc_info = trial_count(art_dir)
    if n_trials != proto["trial_count"]["expected_n_trials"]:
        raise AssertionError(f"trial count {n_trials} != protocol {proto['trial_count']['expected_n_trials']}")
    st = proto["statistics"]["bootstrap"]
    art = art_dir or ROOT / "research_v3/artifacts"
    s3long = pd.read_csv(art / "step3/step3_long.csv").set_index(["variant", "cadence", "trend_weeks", "partition"])
    wanted = set(STRATS)
    rows, series, pool_stats = [], {}, {}
    for part in proto["partitions"]:
        pool = config_pool(part, prepare_signals(part), curve, art_dir)
        assert len(pool) == n_trials, (len(pool), n_trials)
        v_ann = float(np.var(list(pool.values()), ddof=1))
        v_day = annual_to_daily_var(v_ann)
        sr0 = expected_max_sr(v_day, n_trials)
        pool_stats[part] = {"n_configs": len(pool), "var_annual_xsharpe": v_ann, "sd_annual_xsharpe": v_ann ** 0.5,
                            "var_daily": v_day, "sr0_daily": sr0, "sr0_annualised": sr0 * 252 ** 0.5,
                            "pool_mean_xsharpe": float(np.mean(list(pool.values()))),
                            "pool_max_xsharpe": float(np.max(list(pool.values())))}
        view, sessions, weekly, monthly = _setup(part)

        def record(label, variant, cadence, trend, nav, m, frame=None):
            ex = daily_excess(nav, curve)
            met = _metrics(nav, m, curve, frame)
            met["psr0"] = psr(ex, 0.0)
            met["dsr"] = dsr(ex, n_trials, v_day)
            rows.append({"partition": part, "label": label, "variant": variant, "cadence": cadence,
                         "trend_weeks": trend, "post_hoc": variant in POST_HOC, **met})
            series[(part, label, trend)] = ex

        # benchmarks (trend independent): schedule from a v2 model's matched monthly frame
        wf = compute_model_signals_for_events(bars=view.bars, session_dates=sessions, weekly_events=weekly,
                                              model_id="W_M12_T40", momentum_months=12, trend_weeks=40)
        mrows = compute_matched_monthly_signals(wf, monthly, "M_M12_T40").rows
        bench = {k: run_backtest_v3(view, f, V3_DEFAULT, curve) for k, f in benchmark_frames(view, mrows).items()}
        for trend in proto["trend_lengths_all"]:
            for k, (nav, _r, m) in bench.items():
                record(k, k, "M", trend, nav, m)
            for name, c, frame in variant_frames(view, sessions, weekly, monthly, trend, p3, curve):
                if (name, c) not in wanted:
                    continue
                nav, _r, m = run_backtest_v3(view, frame, V3_DEFAULT, curve)
                ref = s3long.loc[(name, c, trend, part)]
                if abs(ref["excess_sharpe"] - excess_sharpe(nav, curve)) > 1e-9 or abs(ref["cagr"] - m["cagr"]) > 1e-9:
                    raise AssertionError(f"step3 reproduction mismatch {name} {c} T{trend} {part}")
                record(f"{name}_{c}", name, c, trend, nav, m, frame)
    df = pd.DataFrame(rows)

    # bootstrap vs 60_40 at headline trend
    head = proto["trend_length_headline"]
    boot = []
    for part in proto["partitions"]:
        ref = series[(part, "60_40", head)]
        idx = stationary_bootstrap_indices(len(ref), st["mean_block_length"], st["n_resamples"], st["seed"])
        for label in [f"{v}_{c}" for v, c in STRATS] + [b for b in BENCH if b != "60_40"]:
            pt, lo, hi = bootstrap_sharpe_diff(series[(part, label, head)], ref, idx, st["ci_level"])
            boot.append({"partition": part, "label": label, "trend_weeks": head, "xsharpe_diff_vs_60_40": pt,
                         "ci_lo": lo, "ci_hi": hi, "ci_excludes_0": bool(lo > 0 or hi < 0)})
    return df, pd.DataFrame(boot), pool_stats, tc_info, n_trials, proto


# ---------------------------------------------------------------- table & selection
def aggregate(df: pd.DataFrame) -> pd.DataFrame:
    """Rows per (partition, label) for trend 40 and the mean over 36/40/44; adds diff vs 60_40 xSharpe."""
    t40 = df[df.trend_weeks == 40].assign(trend="T40")
    mean = df.groupby(["partition", "label", "variant", "cadence", "post_hoc"], as_index=False)[NUM].mean() \
        .assign(trend="mean")
    out = pd.concat([t40.drop(columns=["trend_weeks"]), mean], ignore_index=True)
    ref = out[out.label == "60_40"].set_index(["partition", "trend"])["excess_sharpe"]
    out["xsharpe_vs_60_40"] = [r.excess_sharpe - ref[(r.partition, r.trend)] for r in out.itertuples()]
    return out


def select_candidates(df: pd.DataFrame, agg: pd.DataFrame, rule: dict) -> tuple[list[dict], pd.DataFrame]:
    """Apply the pre-registered rule. Returns (selected, evaluation table of the pool)."""
    ev = []
    idx = df.set_index(["label", "trend_weeks", "partition"])
    for v, c in STRATS:
        if v == "BASE":
            continue
        lab = f"{v}_{c}"
        a = agg[(agg.label == lab) & (agg.trend == "T40")].set_index("partition")
        dsr_val = float(a.loc["validation", "dsr"])
        mdd_ok = all(idx.loc[(lab, t, p), "max_drawdown"] <= idx.loc[(f"BASE_{c}", t, p), "max_drawdown"]
                     for p in ("train", "validation") for t in (36, 40, 44))
        ref = agg[(agg.label == "60_40") & (agg.trend == "T40")].set_index("partition")["excess_sharpe"]
        beat = all(a.loc[p, "excess_sharpe"] > ref[p] for p in ("train", "validation"))
        ev.append({"label": lab, "variant": v, "cadence": c, "post_hoc": v in POST_HOC,
                   "dsr_validation_T40": dsr_val, "cond1_dsr_gt_0.5": bool(dsr_val > 0.5),
                   "cond2_mdd_le_base_all6": bool(mdd_ok), "cond3_beats_60_40_both": bool(beat),
                   "min_xsharpe_T40": float(min(a.loc["train", "excess_sharpe"], a.loc["validation", "excess_sharpe"])),
                   "qualifies": bool(dsr_val > 0.5 and mdd_ok and beat)})
    evd = pd.DataFrame(ev)
    q = evd[evd.qualifies].sort_values("min_xsharpe_T40", ascending=False).head(rule["max_candidates"])
    return q.to_dict("records"), evd


def candidate_params(sel: dict, proto: dict, p3: dict, trend: int) -> dict:
    spec = p3["variants"][sel["variant"]]
    return {"label": sel["label"], "variant": sel["variant"], "cadence": sel["cadence"], "post_hoc": sel["post_hoc"],
            "trend_weeks": trend, "top_n": p3["top_n"], "variant_spec": spec,
            "base_momentum_months": p3["base_momentum_months"],
            "ens_momentum_months": p3["ens_momentum_months"] if spec["ens"] else None,
            "ivol_window": p3["ivol_window"] if spec["ivol"] else None,
            "asset_class_groups": p3["asset_class_groups"] if spec["cap"] else None,
            "cap_rule": p3["cap_rule"] if spec["cap"] else None,
            "defensive_test": p3["defensive_test"] if spec["slots"] == "def" else None,
            "universe": list(UNIVERSE),
            "settings": {"cost_bps": V3_DEFAULT.cost_bps, "slippage_bps": V3_DEFAULT.slippage_bps,
                         "cash_yield_source": V3_DEFAULT.cash_yield_source},
            "selection_evidence": {k: sel[k] for k in ("dsr_validation_T40", "min_xsharpe_T40")}}


def git_head() -> str:
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()


# ---------------------------------------------------------------- outputs
def _p(x):
    return f"{x * 100:.2f}%" if np.isfinite(x) else "n/a"


def _label(r) -> str:
    return r.label.replace("CAND_CAP_DEF", "CAND_CAP_DEF*").replace("CAND_W", "CAND*_W") \
        if r.post_hoc else r.label


def md_table(sub: pd.DataFrame) -> list[str]:
    lines = ["| strategy | CAGR | vol | Sharpe | xSharpe | MDD | worst yr | 2008 | turnover/yr | cash wt | xSh - 60/40 | PSR(0) | DSR |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in sub.itertuples():
        lines.append(f"| {_label(r)} | {_p(r.cagr)} | {_p(r.ann_vol)} | {r.sharpe:.3f} | {r.excess_sharpe:.3f} | "
                     f"{_p(r.max_drawdown)} | {_p(r.worst_year)} | {_p(r.year_2008)} | {r.ann_one_way_turnover:.2f} | "
                     f"{_p(r.mean_cash_weight)} | {r.xsharpe_vs_60_40:+.3f} | {r.psr0:.3f} | {r.dsr:.3f} |")
    return lines


def write_outputs(res, out: Path = OUT_DIR) -> dict:
    df, boot, pool_stats, tc_info, n_trials, proto = res
    out.mkdir(parents=True, exist_ok=True)
    p3 = load_step3()
    agg = aggregate(df)
    df.to_csv(out / "step4_long.csv", index=False)
    agg.to_csv(out / "side_by_side.csv", index=False)
    boot.to_csv(out / "bootstrap_vs_60_40.csv", index=False)
    (out / "trial_count.json").write_text(json.dumps({"n_trials": n_trials, **tc_info, "pool": pool_stats}, indent=2))
    rule = proto["candidate_selection_rule"]
    sel, ev = select_candidates(df, agg, rule)
    ev.to_csv(out / "candidate_evaluation.csv", index=False)
    frozen = {
        "freeze_date": rule["freeze_date"], "code_git_commit": git_head(),
        "commit_note": "HEAD commit at generation time; it EXCLUDES this step's uncommitted files (step 4 code, protocol_step4, REPORT)",
        "protocol_step4_sha256": Path(__file__).with_name("protocol_step4.sha256").read_text().strip(),
        "protocol_step3_sha256": Path(__file__).with_name("protocol_step3.sha256").read_text().strip(),
        "freeze_rule": rule["freeze_rule"], "selection_rule": rule,
        "candidates": [candidate_params(s, proto, p3, proto["trend_length_headline"]) for s in sel],
    }
    (out.parent.parent / "candidates_frozen.json").write_text(json.dumps(frozen, indent=2, default=str))

    L = ["# research_v3 step 4: benchmarks, DSR, bootstrap", "",
         "V3_DEFAULT settings, train/validation only. xSharpe = Sharpe of returns minus IRX accrual. "
         "'xSh - 60/40' = xSharpe minus that of 60/40 (IEF as bond proxy). PSR/DSR on daily excess returns; "
         f"DSR with N={n_trials}. '\\*' = post-hoc candidate. For mean rows PSR/DSR are averages of the per-trend values.", "",
         "## Trial count", "", f"N = {n_trials} = {tc_info['step1_models']} (step 1) + {tc_info['step2_non_base']} (step 2 excl. BASE) + "
         f"{tc_info['step3_non_base']} (step 3 excl. BASE). BASE of steps 2/3 equals step-1 momentum-12 models. "
         "research_v2's 18 originals are the same 18 step-1 models under v2 settings.", ""]
    for part, s in pool_stats.items():
        L.append(f"- {part}: sd of annualised xSharpe across {s['n_configs']} configs = {s['sd_annual_xsharpe']:.3f}; "
                 f"deflation threshold SR0 = {s['sr0_annualised']:.3f} annualised; pool max {s['pool_max_xsharpe']:.3f}.")
    L.append("")
    order = [f"{v}_{c}" for v, c in STRATS] + BENCH
    for trend in ("T40", "mean"):
        for part in proto["partitions"]:
            L += [f"## {part} / {trend}", ""]
            sub = agg[(agg.partition == part) & (agg.trend == trend)].set_index("label").loc[order].reset_index()
            L += md_table(sub) + [""]
    L += ["## Bootstrap 90% CI of xSharpe difference vs 60/40 (T40, stationary bootstrap, block 21, 2000 resamples)", "",
          "| strategy | partition | diff | CI lo | CI hi | excludes 0 |", "|---|---|---|---|---|---|"]
    for r in boot.itertuples():
        L.append(f"| {r.label} | {r.partition} | {r.xsharpe_diff_vs_60_40:+.3f} | {r.ci_lo:+.3f} | {r.ci_hi:+.3f} | {r.ci_excludes_0} |")
    L += ["", "## Candidate selection (pre-registered rule)", "",
          "| candidate pool row | DSR val T40 | MDD<=BASE (6 cells) | beats 60/40 both | qualifies |", "|---|---|---|---|---|"]
    for r in ev.itertuples():
        L.append(f"| {r.label} | {r.dsr_validation_T40:.3f} | {r.cond2_mdd_le_base_all6} | {r.cond3_beats_60_40_both} | {r.qualifies} |")
    L += ["", f"Selected: {[s['label'] for s in sel] or 'none'}"]
    (out / "summary.md").write_text("\n".join(L) + "\n")
    return {"selected": [s["label"] for s in sel]}
