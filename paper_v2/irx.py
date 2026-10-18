"""^IRX cash-yield curve for the CAP_DEF_M shadow.

Base series: research_v3's hash-verified snapshot (2005-01-03..2020-12-31, never modified here).
Forward extension: ^IRX Close for 2021-01-01..as_of, fetched from yfinance at run time (or read from
<bars_dir>/IRX.csv with columns Date, irx_pct in offline mode), persisted under DATA_SNAPSHOTS_DIR as
irx_forward_<hash>.csv + irx_forward_<hash>.json. The snapshot wins on any overlap.
"""
import hashlib
import json
import os
from datetime import datetime, timezone
from typing import Optional, Tuple

import pandas as pd
import yfinance as yf

from research_v3.cash_yield import CashYieldCurve, load_irx

from .config import DATA_SNAPSHOTS_DIR
from .fsutil import atomic_write_text
from .market_data import OFFLINE_ENV

FORWARD_START = "2021-01-01"
OFFLINE_FILE = "IRX.csv"


class IrxError(RuntimeError):
    pass


def _series_csv(s: pd.Series) -> str:
    return pd.DataFrame({"Date": list(s.index), "irx_pct": s.values}).to_csv(index=False)


def _hash_series(s: pd.Series) -> str:
    return hashlib.sha256(_series_csv(s).encode("utf-8")).hexdigest()


def _download_forward(as_of: str) -> pd.Series:
    end = (pd.Timestamp(as_of) + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    raw = yf.download("^IRX", start=FORWARD_START, end=end, auto_adjust=False, progress=False)
    if raw is None or len(raw) == 0:
        raise IrxError("yfinance returned no ^IRX data")
    # Same Close-field handling as research_v3.cash_yield.create_irx_snapshot (no date guard).
    s = raw["Close"]["^IRX"] if isinstance(raw.columns, pd.MultiIndex) else raw["Close"]
    return pd.Series(s.values, index=pd.DatetimeIndex(s.index).strftime("%Y-%m-%d"), dtype=float)


def _read_offline(bars_dir: str) -> pd.Series:
    path = os.path.join(bars_dir, OFFLINE_FILE)
    df = pd.read_csv(path)
    return pd.Series(df["irx_pct"].values, index=df["Date"].astype(str).values, dtype=float)


def _offline_available(bars_dir: Optional[str]) -> bool:
    return bool(bars_dir) and os.path.exists(os.path.join(bars_dir, OFFLINE_FILE))


def load_irx_curve(as_of: str, bars_dir: Optional[str] = None) -> Tuple[CashYieldCurve, str]:
    """Return (CashYieldCurve, irx_data_hash) covering the snapshot plus forward data up to as_of.

    Raises IrxError if as_of is in the forward period and no forward data could be obtained
    (fail closed: never silently forward-fill a stale curve).
    """
    base = load_irx()
    bars_dir = bars_dir or os.environ.get(OFFLINE_ENV)
    series = base
    if as_of >= FORWARD_START:
        try:
            if _offline_available(bars_dir):
                fwd = _read_offline(bars_dir)
            else:
                fwd = _download_forward(as_of)
        except IrxError:
            raise
        except Exception as e:  # network / parsing problems
            raise IrxError(f"could not obtain forward ^IRX data: {e}") from e
        fwd = fwd.dropna()
        fwd = fwd[(fwd.index >= FORWARD_START) & (fwd.index <= as_of)]
        fwd = fwd[~fwd.index.isin(base.index)].sort_index()
        if len(fwd) == 0:
            raise IrxError(f"no forward ^IRX data in [{FORWARD_START}, {as_of}]")
        fhash = _hash_series(fwd)
        meta_path = os.path.join(DATA_SNAPSHOTS_DIR, f"irx_forward_{fhash}.json")
        if not os.path.exists(meta_path):
            os.makedirs(DATA_SNAPSHOTS_DIR, exist_ok=True)
            atomic_write_text(os.path.join(DATA_SNAPSHOTS_DIR, f"irx_forward_{fhash}.csv"), _series_csv(fwd))
            atomic_write_text(meta_path, json.dumps({
                "download_timestamp": datetime.now(timezone.utc).isoformat(),
                "source": f"offline:{bars_dir}/{OFFLINE_FILE}" if _offline_available(bars_dir) else "yfinance ^IRX Close",
                "as_of": as_of, "rows": len(fwd), "first_date": fwd.index[0], "last_date": fwd.index[-1],
                "sha256": fhash,
            }, indent=2))
        series = pd.concat([base, fwd]).sort_index()
    return CashYieldCurve(series), _hash_series(series)
