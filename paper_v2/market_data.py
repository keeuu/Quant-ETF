import yfinance as yf
import pandas as pd
import json
import hashlib
import os
from datetime import datetime, timezone
from typing import Dict, Any, Tuple
from .config import DATA_SNAPSHOTS_DIR, UNIVERSE

def fetch_market_data(as_of_date: str) -> Tuple[Dict[str, pd.DataFrame], str]:
    """Fetch data up to as_of_date, save snapshot metadata, and return bars and hash."""
    bars = {}
    snapshot_meta = {
        "download_timestamp": datetime.now(timezone.utc).isoformat(),
        "as_of": as_of_date,
        "symbols": UNIVERSE,
        "hashes": {}
    }
    
    # In a real environment, we'd only fetch needed history, e.g., 2 years back.
    # We will fetch 'max' for simplicity but filter up to as_of_date.
    all_data_str = ""
    for sym in UNIVERSE:
        df = yf.download(sym, progress=False)
        if hasattr(df.columns, 'levels'):
             df.columns = df.columns.droplevel(1)
        
        # Lowercase columns to match Research V2 format
        df.columns = [c.lower().replace(" ", "_") for c in df.columns]
        
        # Drop tz info if any
        if df.index.tz is not None:
             df.index = df.index.tz_convert(None)
             
        # Filter up to as_of_date
        df = df[df.index <= pd.Timestamp(as_of_date)]
        
        bars[sym] = df
        
        # Hash computation
        df_csv = df.to_csv()
        sym_hash = hashlib.sha256(df_csv.encode('utf-8')).hexdigest()
        snapshot_meta["hashes"][sym] = sym_hash
        all_data_str += sym_hash
        
    combined_hash = hashlib.sha256(all_data_str.encode('utf-8')).hexdigest()
    snapshot_meta["combined_hash"] = combined_hash
    
    # Save snapshot
    snap_path = os.path.join(DATA_SNAPSHOTS_DIR, f"snapshot_{combined_hash}.json")
    with open(snap_path, "w") as f:
        json.dump(snapshot_meta, f, indent=2)
        
    return bars, combined_hash
