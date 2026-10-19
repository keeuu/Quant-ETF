"""Price-consistency check: THS-derived total-return adjusted prices vs Tencent hfq on listed stocks.
Writes artifacts_dev/consistency_check.csv. Usage: python -m research_v3.stock_mf.consistency"""
from __future__ import annotations

import time
from pathlib import Path

import numpy as np
import pandas as pd

from . import guard
from .panel import DATA_DIR, load_dev_stocks

OUT = Path(__file__).resolve().parent / "artifacts_dev"


def main(n_stocks: int = 14):
    import akshare as ak
    tick = pd.read_csv(DATA_DIR / "lists" / "ths_tickers.csv", dtype=str)
    early = {p.stem.replace(".csv", "") for p in (DATA_DIR / "ths_early").glob("*.csv")}
    cand = tick[tick["thscode"].isin(early) & (tick["list_date"] < "2008-01-01")]
    rng = np.random.default_rng(7)
    pick = list(cand.sample(n=min(n_stocks, len(cand)), random_state=7)["thscode"])
    # always include a stock with bonus-share history if available
    stocks = {s.code: s for s in load_dev_stocks(only=set(pick))}
    rows = []
    for thscode in pick:
        code, ex = thscode[:6], thscode[7:]
        if code not in stocks:
            continue
        s = stocks[code]
        time.sleep(1.0)
        h = ak.stock_zh_a_hist_tx(symbol=f"{ex.lower()}{code}", start_date="20080101", end_date="20240331", adjust="hfq")
        h["date"] = h["date"].astype(str).str[:10]
        h = h.set_index("date")
        d = s.df.join(h[["close", "open", "volume", "amount"]].add_prefix("tx_"), how="inner")
        from .panel import tx_total_return_prices
        raw_tx = ak.stock_zh_a_hist_tx(symbol=f"{ex.lower()}{code}", start_date="20080101", end_date="20240331", adjust="")
        raw_tx["date"] = raw_tx["date"].astype(str).str[:10]
        raw_tx = raw_tx.set_index("date").loc[d.index]
        tx_adj, _, n_act, n_chg = tx_total_return_prices(raw_tx["close"].to_numpy(), d["tx_close"].to_numpy(), raw_tx["open"].to_numpy())
        d["tx_adj"] = tx_adj
        d["tx_rawdiff"] = (raw_tx["close"] - d["close"]).abs()
        r_ths = d["adj_close"].pct_change()
        r_tx = d["tx_adj"].pct_change()
        r_hfq = d["tx_close"].pct_change()
        diff = (r_ths - r_tx).abs().dropna()
        raw_diff = (d["close"] / d["tx_volume"].replace(0, np.nan)).notna()
        rows.append({"thscode": thscode, "n_days": len(d), "common_days_frac_of_ths": len(d) / len(s.df),
                     "max_abs_ret_diff": float(diff.max()), "p99_abs_ret_diff": float(diff.quantile(0.99)),
                     "frac_days_diff_gt_0.5pct": float((diff > 0.005).mean()),
                     "max_abs_ret_diff_naive_hfq": float((r_ths - r_hfq).abs().max()),
                     "frac_days_naive_hfq_diff_gt_0.5pct": float(((r_ths - r_hfq).abs() > 0.005).mean()),
                     "max_raw_close_diff": float(d["tx_rawdiff"].max()), "tx_action_days": n_act, "tx_S_changes": n_chg,
                     "total_ret_ths": float(d["adj_close"].iloc[-1] / d["adj_close"].iloc[0] - 1),
                     "total_ret_tx_rebuilt": float(d["tx_adj"].iloc[-1] / d["tx_adj"].iloc[0] - 1),
                     "total_ret_tx_naive_hfq": float(d["tx_close"].iloc[-1] / d["tx_close"].iloc[0] - 1),
                     "vol_ratio_median": float((d["volume"] / d["tx_volume"]).median()),
                     "n_adjustment_days": int((s.exr - 1).abs().gt(1e-9).sum())})
    OUT.mkdir(exist_ok=True)
    df = pd.DataFrame(rows)
    df.to_csv(OUT / "consistency_check.csv", index=False)
    print(df.to_string())


if __name__ == "__main__":
    main()
