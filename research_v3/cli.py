"""python -m research_v3.cli step1 [--out research_v3/artifacts/step1]"""
from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from .cash_yield import create_irx_snapshot
from .runner import ROOT, run_comparison


def _pct(x):
    return f"{x * 100:.2f}%"


def write_summary(df: pd.DataFrame, out: Path) -> None:
    key = ["partition", "model_id"]
    old = df[df.setting == "v2_equiv"].set_index(key)
    new = df[df.setting == "v3_new"].set_index(key)
    cmp = old[["cagr", "sharpe", "max_drawdown", "ann_vol", "ann_one_way_turnover", "mean_cash_weight"]].join(
        new[["cagr", "sharpe", "max_drawdown", "ann_vol", "ann_one_way_turnover", "mean_cash_weight",
             "total_cash_interest", "total_fees"]], lsuffix="_old", rsuffix="_new")
    for c in ("cagr", "sharpe", "max_drawdown"):
        cmp[f"d_{c}"] = cmp[f"{c}_new"] - cmp[f"{c}_old"]
    cmp.reset_index().to_csv(out / "comparison_wide.csv", index=False)
    lines = ["# research_v3 step 1: measurement fixes (cash yield + slippage)", "",
             "Old = v2-equivalent (cash 0%, slippage 0, cost 5 bps). New = ^IRX cash yield, slippage 5 bps (+ cost 5 bps).",
             "Partitions: train and validation only (holdout never touched).", ""]
    for part in cmp.index.get_level_values(0).unique():
        sub = cmp.loc[part]
        lines += [f"## {part}", "",
                  "| model | CAGR old | CAGR new | Sharpe old | Sharpe new | MDD old | MDD new | turnover/yr | cash wt old |",
                  "|---|---|---|---|---|---|---|---|---|"]
        for mid, r in sub.iterrows():
            lines.append(f"| {mid} | {_pct(r.cagr_old)} | {_pct(r.cagr_new)} | {r.sharpe_old:.3f} | {r.sharpe_new:.3f} | "
                         f"{_pct(r.max_drawdown_old)} | {_pct(r.max_drawdown_new)} | {r.ann_one_way_turnover_old:.2f} | "
                         f"{_pct(r.mean_cash_weight_old)} |")
        lines += ["", f"Average delta (new - old) over 18 models: CAGR {sub.d_cagr.mean() * 100:+.3f} pp, "
                      f"Sharpe {sub.d_sharpe.mean():+.3f}, MDD {sub.d_max_drawdown.mean() * 100:+.3f} pp.",
                  f"Average delta, weekly models: CAGR {sub[sub.index.str.startswith('W_')].d_cagr.mean() * 100:+.3f} pp; "
                  f"monthly: {sub[sub.index.str.startswith('M_')].d_cagr.mean() * 100:+.3f} pp.", ""]
    (out / "summary.md").write_text("\n".join(lines) + "\n")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="research_v3.cli")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("snapshot-irx", help="Download ^IRX once (train+validation range) and freeze with sha256")
    s1 = sub.add_parser("step1", help="Run 18 models on train+validation, old vs new settings")
    s1.add_argument("--out", default=str(ROOT / "research_v3/artifacts/step1"))
    s2 = sub.add_parser("step2", help="Pre-registered signal variants vs BASE on train+validation")
    s2.add_argument("--out", default=str(ROOT / "research_v3/artifacts/step2"))
    s3 = sub.add_parser("step3", help="Pre-registered cash-slot / defensive / cap variants vs BASE on train+validation")
    s3.add_argument("--out", default=str(ROOT / "research_v3/artifacts/step3"))
    args = ap.parse_args(argv)
    if args.cmd == "step3":
        from .step3 import run_step3, write_outputs as write3
        out = Path(args.out)
        write3(run_step3(), out)
        print(f"wrote {out}")
        return 0
    if args.cmd == "step2":
        from .step2 import run_step2, write_outputs
        out = Path(args.out)
        write_outputs(run_step2(), out)
        print(f"wrote {out}")
        return 0
    if args.cmd == "snapshot-irx":
        print(create_irx_snapshot())
        return 0
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    df = run_comparison()
    df.to_csv(out / "comparison_long.csv", index=False)
    write_summary(df, out)
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
