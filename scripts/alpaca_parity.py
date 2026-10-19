"""One-off parity check: Alpaca IEX (adjustment=all) vs yfinance Adj Close, daily returns, 8 US symbols.
Not part of the dashboard runtime. Run: python scripts/alpaca_parity.py [years]. Never prints keys."""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dashboard import config
from dashboard.iex_fallback import fetch_alpaca_bars
from dashboard.safety import load_env


def main(years: float = 2.0):
    import yfinance as yf
    from paper_v2.market_data import normalize_yf_frame
    load_env(config.REPO)
    start = (pd.Timestamp.now() - pd.Timedelta(days=int(365 * years))).strftime("%Y-%m-%d")
    for sym in config.us_spec().universe:
        a = fetch_alpaca_bars(sym, start=start).set_index("Date")
        y = normalize_yf_frame(yf.download(sym, start=start, progress=False, auto_adjust=False, actions=True)).set_index("Date")
        idx = a.index.intersection(y.index)
        a, y = a.loc[idx], y.loc[idx]
        ra, ry = a["adj_close"].pct_change().dropna(), y["adj_close"].pct_change().dropna()
        d = ((ra - ry).abs() * 1e4)
        rc = ((a["close"].pct_change() - y["close"].pct_change()).abs() * 1e4).dropna()
        lvl = ((a["close"] / y["close"] - 1).abs() * 1e4)
        out = d[d > 5]
        print(f"{sym} n={len(idx)} alpacaOnly={len(a.index.difference(y.index))} | adj-ret diff bp: max={d.max():.2f} "
              f"median={d.median():.2f} p95={d.quantile(.95):.2f} p99={d.quantile(.99):.2f} | raw-close ret diff max={rc.max():.2f} "
              f"med={rc.median():.2f} | raw close level diff bp max={lvl.max():.1f} med={lvl.median():.1f}")
        if len(out):
            print("   outliers >5bp:", ", ".join(f"{k}:{v:.1f}" for k, v in out.items()))
        # ex-div days per yfinance
        divs = y.index[y["dividends"] > 0] if "dividends" in y else []
        print("   n_div_days=", len(divs), " outliers on/next-to div day:", [k for k in out.index if k in set(divs)])
        # cumulative adj drift
        print(f"   cum adj-return diff over window: {(a['adj_close'].iloc[-1]/a['adj_close'].iloc[0] - y['adj_close'].iloc[-1]/y['adj_close'].iloc[0])*1e4:.1f} bp")


if __name__ == "__main__":
    main(float(sys.argv[1]) if len(sys.argv) > 1 else 2.0)
