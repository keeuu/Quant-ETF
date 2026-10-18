"""Step 3 runner: cash slots / defensive IEF slot / asset-class cap / post-hoc candidates, V3_DEFAULT settings."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from research_v2.signals import compute_matched_monthly_signals, compute_model_signals_for_events

from .cash_yield import CashYieldCurve, load_irx
from .config import V3_DEFAULT
from .engine import run_backtest_v3
from .protocol_step3 import load_verified
from .signals_step3 import make_defensive_provider, reselect3
from .signals_v3 import combine_momentum, make_sigma_provider
from .step2 import _setup, excess_sharpe

METRICS = ["cagr", "ann_vol", "sharpe", "excess_sharpe", "max_drawdown", "ann_one_way_turnover",
           "mean_cash_weight", "mean_ief_def_weight", "worst_year", "year_2008"]
MIN_YEAR_SESSIONS = 200


def calendar_year_returns(nav_df: pd.DataFrame, initial_nav: float) -> pd.DataFrame:
    d = nav_df.assign(year=pd.to_datetime(nav_df["date"]).dt.year)
    last = d.groupby("year")["nav"].last()
    n = d.groupby("year")["nav"].size()
    prev = last.shift(1).fillna(initial_nav)
    return pd.DataFrame({"ret": last / prev - 1.0, "sessions": n})


def mean_def_weight(nav_df: pd.DataFrame, frame) -> float:
    """Time-weighted mean of the weight sitting in IEF through defensive slots (from signal rows)."""
    by_exec = sorted((r["execution_session"], r.get("def_ief_weight", 0.0)) for r in frame.rows
                     if r.get("status") == "COMPLETE")
    ex = [e for e, _ in by_exec]
    vals = []
    import bisect
    for d in nav_df["date"]:
        i = bisect.bisect_right(ex, d) - 1
        vals.append(by_exec[i][1] if i >= 0 else 0.0)
    return float(np.mean(vals))


def variant_frames(view, sessions, weekly, monthly, trend: int, proto: dict, curve: CashYieldCurve):
    """Yield (variant, cadence, SignalFrame)."""
    base_m = proto["base_momentum_months"]
    months = sorted(set(proto["ens_momentum_months"]) | {base_m})
    raw = {m: compute_model_signals_for_events(
        bars=view.bars, session_dates=sessions, weekly_events=weekly, model_id=f"W_M{m:02d}_T{trend}",
        momentum_months=m, trend_weeks=trend) for m in months}
    base_w = raw[base_m]
    ens_w = combine_momentum([raw[m] for m in proto["ens_momentum_months"]], f"W_ENS_T{trend}")
    sigma = make_sigma_provider(view.bars, proto["ivol_window"])
    dt = proto["defensive_test"]
    defensive = make_defensive_provider(view.bars, sessions, curve, dt["symbol"], dt["months"])
    groups = proto["asset_class_groups"]
    top_n = proto["top_n"]
    for name, spec in proto["variants"].items():
        wf = ens_w if spec["ens"] else base_w
        for c in spec["cadences"]:
            f = wf if c == "W" else compute_matched_monthly_signals(wf, monthly, "M_" + wf.model_id[2:])
            if name == "BASE":
                yield name, c, f
                continue
            yield name, c, reselect3(
                f, f"{c}_{name}_T{trend}", cap_groups=groups if spec["cap"] else None, slots=spec["slots"],
                sigma=sigma if spec["ivol"] else None, defensive=defensive if spec["slots"] == "def" else None,
                top_n=top_n)


def run_step3(partitions=None) -> pd.DataFrame:
    proto = load_verified()  # refuses to run if changed
    partitions = partitions or proto["partitions"]
    curve = CashYieldCurve(load_irx())
    rows = []
    for part in partitions:
        view, sessions, weekly, monthly = _setup(part)
        for trend in proto["trend_weeks"]:
            for name, c, frame in variant_frames(view, sessions, weekly, monthly, trend, proto, curve):
                nav, _reb, m = run_backtest_v3(view, frame, V3_DEFAULT, curve)
                yr = calendar_year_returns(nav, V3_DEFAULT.initial_nav)
                full = yr[yr["sessions"] >= MIN_YEAR_SESSIONS]["ret"]
                rows.append({
                    "variant": name, "post_hoc": bool(proto["variants"][name].get("post_hoc", False)),
                    "cadence": c, "trend_weeks": trend, "partition": part,
                    "cagr": m["cagr"], "ann_vol": m["annualized_volatility"],
                    "sharpe": m["sharpe_ratio"] if m["sharpe_ratio"] is not None else float("nan"),
                    "excess_sharpe": excess_sharpe(nav, curve), "max_drawdown": m["max_drawdown"],
                    "ann_one_way_turnover": m["annualized_one_way_turnover"],
                    "mean_cash_weight": m["mean_cash_weight"],
                    "mean_ief_def_weight": mean_def_weight(nav, frame),
                    "worst_year": float(full.min()) if len(full) else float("nan"),
                    "year_2008": float(yr.loc[2008, "ret"]) if 2008 in yr.index and yr.loc[2008, "sessions"] >= MIN_YEAR_SESSIONS else float("nan"),
                })
    return pd.DataFrame(rows)


ORDER = ["BASE", "CASHSLOT", "DEF", "CAP", "CAP_DEF", "CAND", "CAND_CAP_DEF"]
POST_HOC = {"CAND", "CAND_CAP_DEF"}


def _lab(v: str) -> str:
    return v + "*" if v in POST_HOC else v


def write_outputs(df: pd.DataFrame, out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    df.to_csv(out / "step3_long.csv", index=False)
    wide = df.pivot_table(index=["variant", "cadence", "trend_weeks"], columns="partition", values=METRICS)
    wide.columns = [f"{m}_{p}" for m, p in wide.columns]
    wide.reset_index().to_csv(out / "step3_wide.csv", index=False)

    mean = df.groupby(["variant", "cadence", "partition"])[METRICS].mean()
    lines = ["# research_v3 step 3: cash slots, defensive slot, asset-class cap", "",
             "Settings: V3_DEFAULT (^IRX cash, 5 bps cost + 5 bps slippage). Train and validation only. "
             "Means across trend lengths 36/40/44. Deltas vs BASE of the same cadence/partition. "
             "Sharpe rf=0; xSharpe = Sharpe of returns minus IRX daily accrual. "
             "Worst yr = worst calendar-year return (years with >=200 sessions); 2008 = calendar 2008 return (train only). "
             "IEF-def = mean weight held in IEF via defensive slots.", "",
             "\\* post-hoc candidate chosen after seeing step-2 results (not pre-registered as a hypothesis); weekly only.", ""]
    for part in ("train", "validation"):
        for c in ("W", "M"):
            lines += [f"## {part} / {c}", "",
                      "| variant | CAGR | dCAGR pp | Sharpe | dSharpe | xSharpe | dxSharpe | MDD | dMDD pp | worst yr | 2008 | cash wt | IEF-def | turnover/yr |",
                      "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
            b = mean.loc[("BASE", c, part)]
            for v in ORDER:
                if (v, c, part) not in mean.index:
                    continue
                r = mean.loc[(v, c, part)]
                y08 = f"{r.year_2008*100:.2f}%" if np.isfinite(r.year_2008) else "n/a"
                lines.append(
                    f"| {_lab(v)} | {r.cagr*100:.2f}% | {(r.cagr-b.cagr)*100:+.2f} | {r.sharpe:.3f} | {r.sharpe-b.sharpe:+.3f} | "
                    f"{r.excess_sharpe:.3f} | {r.excess_sharpe-b.excess_sharpe:+.3f} | {r.max_drawdown*100:.2f}% | "
                    f"{(r.max_drawdown-b.max_drawdown)*100:+.2f} | {r.worst_year*100:.2f}% | {y08} | "
                    f"{r.mean_cash_weight*100:.1f}% | {r.mean_ief_def_weight*100:.1f}% | {r.ann_one_way_turnover:.2f} |")
            lines.append("")
    lines += ["## Consistency of improvement vs BASE", "",
              "Sign of delta across all 6 cells (train/validation x trend 36/40/44), same cadence. "
              "'consistent+' = better in all 6, 'consistent-' = worse in all 6, else mixed (n+ of 6 better).", "",
              "| variant | cadence | CAGR | Sharpe | xSharpe | MDD (lower is better) |", "|---|---|---|---|---|---|"]
    idx = df.set_index(["variant", "cadence", "trend_weeks", "partition"])
    for v in ORDER[1:]:
        for c in ("W", "M"):
            if (v, c, 36, "train") not in idx.index:
                continue
            cells = []
            for m, sgn in (("cagr", 1), ("sharpe", 1), ("excess_sharpe", 1), ("max_drawdown", -1)):
                d = [sgn * (idx.loc[(v, c, t, p), m] - idx.loc[("BASE", c, t, p), m])
                     for p in ("train", "validation") for t in (36, 40, 44)]
                npos = sum(x > 0 for x in d)
                cells.append("consistent+" if npos == 6 else "consistent-" if npos == 0 else f"mixed ({npos}/6)")
            lines.append(f"| {_lab(v)} | {c} | " + " | ".join(cells) + " |")
    (out / "summary.md").write_text("\n".join(lines) + "\n")
