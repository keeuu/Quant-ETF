"""Cash yield from the 13-week T-bill index ^IRX (yfinance), snapshotted + hash-verified.

Conventions (documented design choices)
---------------------------------------
* ^IRX quotes the 13-week bill *discount* rate d in annualized percent. We convert to a
  bond-equivalent (investment) yield for a 91-day bill:
      y = (d/100 * 365/360) / (1 - d/100 * 91/360)
  Negative/zero rates are allowed (y may be <= 0); accrual just follows the sign.
* Accrual is per CALENDAR day elapsed between consecutive scored sessions (so weekends/holidays
  accrue): cash *= 1 + y_prev * days / 365, credited at the start of session t.
* y_prev is the rate published at the PREVIOUS session (t-1) close, forward-filled from earlier
  dates only (e.g. bond-market holidays). No look-ahead: the rate dated t (or later) never
  influences accrual credited at t. Fails if no prior value exists.
* The first scored session has no previous session, so it accrues nothing.
* Snapshot covers 2005-01-03..2020-12-31 only (train+validation); holdout dates are not fetched.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pandas as pd

from .guards import MAX_ALLOWED_DATE, assert_date_allowed

DATA_DIR = Path(__file__).resolve().parent / "data"
IRX_CSV = "irx_20050103_20201231.csv"
IRX_MANIFEST = "irx_manifest.json"
BILL_DAYS = 91


class CashYieldError(ValueError):
    pass


def sha256_file(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def discount_to_bey(d_pct: float, bill_days: int = BILL_DAYS) -> float:
    d = d_pct / 100.0
    return (d * 365.0 / 360.0) / (1.0 - d * bill_days / 360.0)


def create_irx_snapshot(data_dir: Path = DATA_DIR, start: str = "2005-01-03", end: str = MAX_ALLOWED_DATE) -> dict:
    """Download ^IRX once and freeze it (network). end is capped at the validation end."""
    assert_date_allowed(end)
    import yfinance as yf  # local import: only needed for snapshotting

    raw = yf.download("^IRX", start=start, end=(pd.Timestamp(end) + pd.Timedelta(days=1)).strftime("%Y-%m-%d"),
                      auto_adjust=False, progress=False)
    s = raw["Close"]["^IRX"] if isinstance(raw.columns, pd.MultiIndex) else raw["Close"]
    df = pd.DataFrame({"Date": s.index.strftime("%Y-%m-%d"), "irx_pct": s.values})
    df = df[df["Date"] <= end].reset_index(drop=True)
    data_dir.mkdir(parents=True, exist_ok=True)
    csv_path = data_dir / IRX_CSV
    df.to_csv(csv_path, index=False)
    manifest = {
        "source": "yfinance ^IRX Close (13-week T-bill discount rate, annualized percent)",
        "file": IRX_CSV,
        "sha256": sha256_file(csv_path),
        "rows": len(df),
        "first_date": df["Date"].iloc[0],
        "last_date": df["Date"].iloc[-1],
        "na_rows": int(df["irx_pct"].isna().sum()),
        "yfinance_version": yf.__version__,
        "created": pd.Timestamp.now(tz="UTC").isoformat(),
    }
    (data_dir / IRX_MANIFEST).write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return manifest


def load_irx(data_dir: Path = DATA_DIR) -> pd.Series:
    """Load snapshot, verify sha256 against manifest, return Series indexed by 'YYYY-MM-DD' (percent)."""
    data_dir = Path(data_dir)
    manifest = json.loads((data_dir / IRX_MANIFEST).read_text())
    csv_path = data_dir / manifest["file"]
    actual = sha256_file(csv_path)
    if actual != manifest["sha256"]:
        raise CashYieldError(f"IRX snapshot hash mismatch: manifest {manifest['sha256']} vs actual {actual}")
    df = pd.read_csv(csv_path)
    if df["Date"].max() > MAX_ALLOWED_DATE:
        raise CashYieldError("IRX snapshot contains dates beyond validation end")
    return pd.Series(df["irx_pct"].values, index=df["Date"].astype(str).values, dtype=float)


class CashYieldCurve:
    """Maps a session date to the daily-accrual-ready bond-equivalent yield known at that date's close."""

    def __init__(self, irx_pct: pd.Series):
        s = irx_pct.sort_index()
        if not s.index.is_unique:
            raise CashYieldError("duplicate IRX dates")
        self._s = s.dropna()  # missing values are filled forward from earlier values only
        self._dates = list(self._s.index)
        self._vals = self._s.values

    def pct_as_of(self, date: str) -> float:
        """Latest available discount rate (percent) dated <= date. Fails if none."""
        import bisect
        i = bisect.bisect_right(self._dates, date) - 1
        if i < 0:
            raise CashYieldError(f"No IRX value on or before {date}")
        return float(self._vals[i])

    def yield_as_of(self, date: str) -> float:
        return discount_to_bey(self.pct_as_of(date))

    def accrual_factor(self, prev_session: str, session: str) -> float:
        """Growth factor credited at `session` for cash held since `prev_session` close."""
        days = (pd.Timestamp(session) - pd.Timestamp(prev_session)).days
        if days <= 0:
            raise CashYieldError(f"Non-increasing sessions {prev_session} -> {session}")
        return 1.0 + self.yield_as_of(prev_session) * days / 365.0
