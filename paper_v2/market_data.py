import hashlib
import json
import os
from datetime import datetime, timezone
from typing import Dict, Optional, Tuple

import pandas as pd
import yfinance as yf

from .config import DATA_SNAPSHOTS_DIR, UNIVERSE
from .fsutil import atomic_write_text

OFFLINE_ENV = "PAPER_V2_OFFLINE_BARS_DIR"

# research_v2 REQUIRED_COLUMNS plus a trailing per-share cash `dividends` column.
BAR_COLUMNS = ["Date", "open", "high", "low", "close", "adj_close", "adj_open", "volume", "dividends"]

_RENAME = {
    "open": "open", "high": "high", "low": "low", "close": "close",
    "adj close": "adj_close", "adj_close": "adj_close",
    "volume": "volume", "dividends": "dividends",
}


def normalize_yf_frame(raw: pd.DataFrame, as_of_date: Optional[str] = None) -> pd.DataFrame:
    """Convert a yfinance frame (auto_adjust=False, actions=True) to research_v2 bar format.

    Output columns: Date (YYYY-MM-DD string), open, high, low, close, adj_close,
    adj_open (= open * adj_close / close), volume, dividends. Rows after as_of_date dropped.
    """
    df = raw.copy()
    if isinstance(df.columns, pd.MultiIndex):
        # yfinance returns (Price, Ticker); keep the level that holds the field names.
        field_level = 0
        for lvl in range(df.columns.nlevels):
            if any(str(v).lower() == "close" for v in df.columns.get_level_values(lvl)):
                field_level = lvl
                break
        df.columns = df.columns.get_level_values(field_level)
    df.columns = [_RENAME.get(str(c).strip().lower(), str(c).strip().lower().replace(" ", "_")) for c in df.columns]
    df = df.loc[:, ~pd.Index(df.columns).duplicated()]

    if "adj_close" not in df.columns:
        raise ValueError("yfinance frame lacks 'Adj Close'; download with auto_adjust=False")
    if "dividends" not in df.columns:
        df["dividends"] = 0.0

    idx = pd.DatetimeIndex(df.index)
    if idx.tz is not None:
        idx = idx.tz_localize(None)  # yfinance index is exchange-local midnight; keep the wall date
    df.index = idx.normalize()
    df.index.name = "Date"
    df = df[~df.index.duplicated(keep="last")].sort_index()
    df = df.dropna(subset=["close", "adj_close"])
    df["dividends"] = df["dividends"].fillna(0.0)
    df["adj_open"] = df["open"] * df["adj_close"] / df["close"]
    if as_of_date is not None:
        df = df[df.index <= pd.Timestamp(as_of_date)]
    df = df.reset_index()
    df["Date"] = df["Date"].dt.strftime("%Y-%m-%d")
    return df[BAR_COLUMNS].reset_index(drop=True)


def _load_offline(sym: str, bars_dir: str, as_of_date: str) -> pd.DataFrame:
    df = pd.read_csv(os.path.join(bars_dir, f"{sym}.csv"))
    if "dividends" not in df.columns:
        df["dividends"] = 0.0
    df = df[df["Date"] <= as_of_date]
    return df[BAR_COLUMNS].reset_index(drop=True)


def fetch_market_data(as_of_date: str, bars_dir: Optional[str] = None) -> Tuple[Dict[str, pd.DataFrame], str]:
    """Fetch bars up to as_of_date (YYYY-MM-DD), persist them, and return (bars, combined_hash).

    Offline mode: if bars_dir (or env PAPER_V2_OFFLINE_BARS_DIR) is set, read <SYM>.csv files
    in research_v2 snapshot format instead of downloading (optional `dividends` column).
    Bars are persisted to DATA_SNAPSHOTS_DIR/bars_<hash>/<SYM>.csv for reproducibility.
    """
    bars_dir = bars_dir or os.environ.get(OFFLINE_ENV)
    bars: Dict[str, pd.DataFrame] = {}
    snapshot_meta = {
        "download_timestamp": datetime.now(timezone.utc).isoformat(),
        "as_of": as_of_date,
        "symbols": UNIVERSE,
        "source": f"offline:{bars_dir}" if bars_dir else "yfinance",
        "hashes": {},
    }

    all_data_str = ""
    for sym in UNIVERSE:
        if bars_dir:
            df = _load_offline(sym, bars_dir, as_of_date)
        else:
            raw = yf.download(sym, progress=False, auto_adjust=False, actions=True)
            df = normalize_yf_frame(raw, as_of_date)
        bars[sym] = df
        sym_hash = hashlib.sha256(df.to_csv(index=False).encode("utf-8")).hexdigest()
        snapshot_meta["hashes"][sym] = sym_hash
        all_data_str += sym_hash

    combined_hash = hashlib.sha256(all_data_str.encode("utf-8")).hexdigest()
    snapshot_meta["combined_hash"] = combined_hash

    bars_path = os.path.join(DATA_SNAPSHOTS_DIR, f"bars_{combined_hash}")
    snapshot_meta["bars_dir"] = bars_path
    if not os.path.isdir(bars_path):
        os.makedirs(bars_path, exist_ok=True)
        for sym, df in bars.items():
            atomic_write_text(os.path.join(bars_path, f"{sym}.csv"), df.to_csv(index=False))
    atomic_write_text(
        os.path.join(DATA_SNAPSHOTS_DIR, f"snapshot_{combined_hash}.json"),
        json.dumps(snapshot_meta, indent=2),
    )
    return bars, combined_hash
