"""Run all 18 research_v2 models (9 weekly + 9 matched monthly) on train/validation only."""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from research_v2.calendar import market_as_of_after_close
from research_v2.data import load_dataset
from research_v2.schedule import build_monthly_matched_schedule, build_weekly_schedule
from research_v2.signals import compute_matched_monthly_signals, compute_model_signals_for_events
from research_v2.splits import PARTITIONS

from .cash_yield import CashYieldCurve, load_irx
from .config import V2_EQUIVALENT, V3_DEFAULT, V3Config
from .engine import run_backtest_v3
from .guards import assert_partition_allowed

ROOT = Path(__file__).resolve().parents[1]


def prepare_signals(partition: str):
    """Return (dataset_view, {model_id: SignalFrame}) for all 18 models on one allowed partition."""
    assert_partition_allowed(partition)
    protocol = json.loads((ROOT / "research_v2/protocol.json").read_text())
    manifest = json.loads((ROOT / "docs/research_v2/dataset_manifest.json").read_text())
    snapshot_dir = ROOT / "research_v2" / "data" / manifest["dataset_id"]
    view = load_dataset(snapshot_dir=snapshot_dir, partition=partition, as_of=manifest["as_of"],
                        splits=protocol["splits"])
    end = PARTITIONS[partition]["end"]
    as_of = market_as_of_after_close(end)
    session_dates = [s for s in pd.read_csv(snapshot_dir / "session_table.csv")["session_date"].tolist() if s <= end]
    weekly = build_weekly_schedule(session_dates, as_of=as_of)
    monthly = build_monthly_matched_schedule(session_dates, as_of=as_of, weekly_events=weekly)
    frames = {}
    for spec in protocol["model_grid"]["models"]:
        wf = compute_model_signals_for_events(
            bars=view.bars, session_dates=session_dates, weekly_events=weekly, model_id=spec["model_id"],
            momentum_months=spec["momentum_months"], trend_weeks=spec["trend_weeks"])
        frames[spec["model_id"]] = wf
        mid = "M_" + spec["model_id"][2:]
        frames[mid] = compute_matched_monthly_signals(wf, monthly, mid)
    return view, frames


def run_comparison(partitions=("train", "validation"), out_rows=None):
    curve = CashYieldCurve(load_irx())
    settings = {"v2_equiv": V2_EQUIVALENT, "v3_new": V3_DEFAULT}
    rows = []
    for part in partitions:
        view, frames = prepare_signals(part)
        for mid, sf in frames.items():
            for label, cfg in settings.items():
                _nav, reb, m = run_backtest_v3(view, sf, cfg, curve)
                rows.append({
                    "partition": part, "model_id": mid, "setting": label,
                    "cash_yield_source": cfg.cash_yield_source, "slippage_bps": cfg.slippage_bps,
                    "cagr": m["cagr"], "ann_vol": m["annualized_volatility"], "sharpe": m["sharpe_ratio"],
                    "max_drawdown": m["max_drawdown"], "final_nav": m["final_nav"],
                    "ann_one_way_turnover": m["annualized_one_way_turnover"],
                    "rebalance_count": m["rebalance_count"], "total_fees": m["total_fees"],
                    "mean_cash_weight": m["mean_cash_weight"], "pct_days_all_cash": m["pct_days_all_cash"],
                    "total_cash_interest": m["total_cash_interest"],
                })
    return pd.DataFrame(rows)
