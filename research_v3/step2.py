"""Step 2 runner: ENS / BUF / IVOL / TRANCHE / COMBO vs BASE on train+validation (V3_DEFAULT settings)."""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

from research_v2.calendar import market_as_of_after_close
from research_v2.data import load_dataset
from research_v2.schedule import build_monthly_matched_schedule, build_weekly_schedule
from research_v2.signals import compute_matched_monthly_signals, compute_model_signals_for_events
from research_v2.splits import PARTITIONS

from .cash_yield import CashYieldCurve, load_irx
from .config import V3_DEFAULT
from .engine import run_backtest_v3
from .guards import assert_partition_allowed
from .protocol_step2 import load_verified
from .signals_v3 import combine_momentum, make_sigma_provider, reselect, tranche_frames
from .tranche import run_tranches

ROOT = Path(__file__).resolve().parents[1]


def excess_sharpe(nav_df: pd.DataFrame, curve: CashYieldCurve) -> float:
    d = nav_df["date"].tolist()
    rf = np.array([0.0] + [curve.accrual_factor(d[i - 1], d[i]) - 1.0 for i in range(1, len(d))])
    ex = nav_df["daily_return"].to_numpy(dtype=float) - rf
    sd = ex.std(ddof=1)
    return float(ex.mean() / sd * math.sqrt(252.0)) if sd > 1e-12 else float("nan")


def _setup(partition: str):
    assert_partition_allowed(partition)
    protocol = json.loads((ROOT / "research_v2/protocol.json").read_text())
    manifest = json.loads((ROOT / "docs/research_v2/dataset_manifest.json").read_text())
    snapshot_dir = ROOT / "research_v2" / "data" / manifest["dataset_id"]
    view = load_dataset(snapshot_dir=snapshot_dir, partition=partition, as_of=manifest["as_of"],
                        splits=protocol["splits"])
    end = PARTITIONS[partition]["end"]
    as_of = market_as_of_after_close(end)
    sessions = [s for s in pd.read_csv(snapshot_dir / "session_table.csv")["session_date"].tolist() if s <= end]
    weekly = build_weekly_schedule(sessions, as_of=as_of)
    monthly = build_monthly_matched_schedule(sessions, as_of=as_of, weekly_events=weekly)
    return view, sessions, weekly, monthly


def variant_runs(view, sessions, weekly, monthly, trend: int, proto: dict):
    """Yield (variant, cadence, kind, payload); kind 'single' -> SignalFrame, 'tranche' -> list of frames."""
    v = proto["variants"]
    base_m = proto["base_momentum_months"]
    raw = {m: compute_model_signals_for_events(
        bars=view.bars, session_dates=sessions, weekly_events=weekly, model_id=f"W_M{m:02d}_T{trend}",
        momentum_months=m, trend_weeks=trend) for m in sorted(set(v["ENS"]["momentum_months"]) | {base_m})}
    base_w = raw[base_m]
    ens_w = combine_momentum([raw[m] for m in v["ENS"]["momentum_months"]], f"W_ENS_T{trend}")
    sigma = make_sigma_provider(view.bars, v["IVOL"]["vol_window"])
    buf = v["BUF"]["buffer_rank"]
    nt = v["TRANCHE"]["n_tranches"]

    def cad(wf, c):
        return wf if c == "W" else compute_matched_monthly_signals(wf, monthly, "M_" + wf.model_id[2:])

    specs = {  # variant -> (weekly base, reselect kwargs)
        "BASE": (base_w, None), "ENS": (ens_w, None),
        "BUF": (base_w, dict(buffer_rank=buf)), "IVOL": (base_w, dict(sigma=sigma)),
        "COMBO": (ens_w, dict(buffer_rank=buf, sigma=sigma)),
    }
    for name, (wf, kw) in specs.items():
        for c in ("W", "M"):
            f = cad(wf, c)
            yield name, c, "single", (reselect(f, **kw) if kw else f)
    yield "TRANCHE", "M", "tranche", tranche_frames(base_w, f"M_TR_T{trend}", nt)
    yield "COMBO", "M", "tranche", tranche_frames(ens_w, f"M_COMBOTR_T{trend}", nt, buffer_rank=buf, sigma=sigma)


def run_step2(partitions=("train", "validation")) -> pd.DataFrame:
    proto = load_verified()  # refuses to run if changed
    curve = CashYieldCurve(load_irx())
    rows = []
    for part in partitions:
        view, sessions, weekly, monthly = _setup(part)
        for trend in proto["trend_weeks"]:
            for name, c, kind, payload in variant_runs(view, sessions, weekly, monthly, trend, proto):
                if kind == "single":
                    nav, _reb, m = run_backtest_v3(view, payload, V3_DEFAULT, curve)
                else:
                    nav, _reb, m = run_tranches(view, payload, V3_DEFAULT, curve)
                label = name + ("+TRANCHE" if kind == "tranche" and name == "COMBO" else "")
                rows.append({
                    "variant": label, "cadence": c, "trend_weeks": trend, "partition": part,
                    "cagr": m["cagr"], "ann_vol": m["annualized_volatility"],
                    "sharpe": m["sharpe_ratio"] if m["sharpe_ratio"] is not None else float("nan"),
                    "excess_sharpe": excess_sharpe(nav, curve), "max_drawdown": m["max_drawdown"],
                    "ann_one_way_turnover": m["annualized_one_way_turnover"],
                    "mean_cash_weight": m["mean_cash_weight"],
                })
    return pd.DataFrame(rows)


METRICS = ["cagr", "ann_vol", "sharpe", "excess_sharpe", "max_drawdown", "ann_one_way_turnover", "mean_cash_weight"]


def write_outputs(df: pd.DataFrame, out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    df.to_csv(out / "step2_long.csv", index=False)
    wide = df.pivot_table(index=["variant", "cadence", "trend_weeks"], columns="partition", values=METRICS)
    wide.columns = [f"{m}_{p}" for m, p in wide.columns]
    wide.reset_index().to_csv(out / "step2_wide.csv", index=False)

    mean = df.groupby(["variant", "cadence", "partition"])[METRICS].mean()
    order = ["BASE", "ENS", "BUF", "IVOL", "TRANCHE", "COMBO", "COMBO+TRANCHE"]
    lines = ["# research_v3 step 2: signal-variant comparison", "",
             "Settings: V3_DEFAULT (^IRX cash, 5 bps cost + 5 bps slippage). Train and validation only. "
             "Means across trend lengths 36/40/44. Deltas vs BASE of the same cadence/partition. "
             "Sharpe rf=0; xSharpe = Sharpe of returns minus IRX daily accrual.", ""]
    for part in ("train", "validation"):
        for c in ("W", "M"):
            lines += [f"## {part} / {c}", "",
                      "| variant | CAGR | dCAGR pp | Sharpe | dSharpe | xSharpe | dxSharpe | MDD | dMDD pp | turnover/yr | cash wt |",
                      "|---|---|---|---|---|---|---|---|---|---|---|"]
            b = mean.loc[("BASE", c, part)]
            for v in order:
                if (v, c, part) not in mean.index:
                    continue
                r = mean.loc[(v, c, part)]
                lines.append(
                    f"| {v} | {r.cagr*100:.2f}% | {(r.cagr-b.cagr)*100:+.2f} | {r.sharpe:.3f} | {r.sharpe-b.sharpe:+.3f} | "
                    f"{r.excess_sharpe:.3f} | {r.excess_sharpe-b.excess_sharpe:+.3f} | {r.max_drawdown*100:.2f}% | "
                    f"{(r.max_drawdown-b.max_drawdown)*100:+.2f} | {r.ann_one_way_turnover:.2f} | {r.mean_cash_weight*100:.1f}% |")
            lines.append("")
    lines += ["## Consistency of improvement vs BASE", "",
              "Sign of delta across all 6 cells (train/validation x trend 36/40/44), same cadence. "
              "'consistent+' = better in all 6, 'consistent-' = worse in all 6, else mixed (n+ of 6 better).", "",
              "| variant | cadence | CAGR | Sharpe | xSharpe | MDD (lower is better) |", "|---|---|---|---|---|---|"]
    idx = df.set_index(["variant", "cadence", "trend_weeks", "partition"])
    for v in order[1:]:
        for c in ("W", "M"):
            if (v, c, 36, "train") not in idx.index:
                continue
            cells = []
            for m, sgn in (("cagr", 1), ("sharpe", 1), ("excess_sharpe", 1), ("max_drawdown", -1)):
                d = [sgn * (idx.loc[(v, c, t, p), m] - idx.loc[("BASE", c, t, p), m])
                     for p in ("train", "validation") for t in (36, 40, 44)]
                npos = sum(x > 0 for x in d)
                cells.append("consistent+" if npos == 6 else "consistent-" if npos == 0 else f"mixed ({npos}/6)")
            lines.append(f"| {v} | {c} | " + " | ".join(cells) + " |")
    (out / "summary.md").write_text("\n".join(lines) + "\n")
