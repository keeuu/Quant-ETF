"""Offline data snapshotting, strict session validation, and partition scoping.

Ensures byte-level audit trail, fail-closed missing data detection,
and prevents forward-looking leakage.
"""
from __future__ import annotations

import math
import os
import shutil
from pathlib import Path
from typing import Any, Mapping, Sequence

import pandas as pd

from .calendar import build_session_table, parse_as_of
from .contracts import DatasetManifest, DatasetView, UNIVERSE
from .provenance import canonical_json, object_hash, sha256

REQUIRED_COLUMNS = ["Date", "open", "high", "low", "close", "adj_close", "adj_open", "volume"]


class DataValidationError(ValueError):
    """Raised when data integrity or session alignment fails."""
    pass


def copy_verify(source_file: Path, target_file: Path) -> str:
    """Byte-for-byte copy with pre/post sha256 verification."""
    source_bytes = source_file.read_bytes()
    source_hash = sha256(source_bytes)

    target_file.parent.mkdir(parents=True, exist_ok=True)
    target_file.write_bytes(source_bytes)

    target_hash = sha256(target_file.read_bytes())
    if source_hash != target_hash:
        raise DataValidationError(f"Hash mismatch during copy: {source_file} -> {target_file}")
    return source_hash


def validate_etf_series(
    df: pd.DataFrame,
    symbol: str,
    expected_sessions: Sequence[str],
) -> list[dict[str, Any]]:
    """Strictly validate a single ETF's price series.

    Returns a list of detected issues. If empty, the series is valid.
    """
    issues: list[dict[str, Any]] = []

    # 1. Column structure
    missing_cols = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing_cols:
        issues.append({
            "symbol": symbol,
            "type": "missing_columns",
            "columns": missing_cols,
        })
        return issues

    # 2. Date order & uniqueness
    dates = df["Date"].tolist()
    if not df["Date"].is_monotonic_increasing:
        issues.append({
            "symbol": symbol,
            "type": "dates_not_increasing",
        })

    duplicates = df[df["Date"].duplicated()]["Date"].tolist()
    if duplicates:
        issues.append({
            "symbol": symbol,
            "type": "duplicate_dates",
            "dates": duplicates,
        })

    # 3. Non-finite values, prices > 0, volume >= 0
    numeric_cols = ["open", "high", "low", "close", "adj_close", "adj_open"]
    for col in numeric_cols:
        if df[col].isna().any() or np_is_inf(df[col]):
            issues.append({
                "symbol": symbol,
                "type": "non_finite_values",
                "column": col,
            })
        if (df[col] <= 0).any():
            issues.append({
                "symbol": symbol,
                "type": "non_positive_price",
                "column": col,
            })

    if df["volume"].isna().any() or np_is_inf(df["volume"]) or (df["volume"] < 0).any():
        issues.append({
            "symbol": symbol,
            "type": "invalid_volume",
        })

    # 4. OHLC logical relationships
    # high >= low, high >= open, high >= close, low <= open, low <= close
    invalid_hl = df[df["high"] < df["low"]]
    if not invalid_hl.empty:
        issues.append({
            "symbol": symbol,
            "type": "high_less_than_low",
            "dates": invalid_hl["Date"].tolist(),
        })

    invalid_h_oc = df[(df["high"] < df["open"]) | (df["high"] < df["close"])]
    if not invalid_h_oc.empty:
        issues.append({
            "symbol": symbol,
            "type": "high_less_than_open_close",
            "dates": invalid_h_oc["Date"].tolist(),
        })

    invalid_l_oc = df[(df["low"] > df["open"]) | (df["low"] > df["close"])]
    if not invalid_l_oc.empty:
        issues.append({
            "symbol": symbol,
            "type": "low_greater_than_open_close",
            "dates": invalid_l_oc["Date"].tolist(),
        })

    # 5. Adjusted open formula consistency
    # adj_open = raw_open * adj_close / raw_close
    expected_adj_open = df["open"] * (df["adj_close"] / df["close"])
    diff = (df["adj_open"] - expected_adj_open).abs()
    rel_diff = diff / df["adj_open"]
    # Allow small numerical rounding tolerance
    bad_adj_open = df[rel_diff > 1e-4]
    if not bad_adj_open.empty:
        issues.append({
            "symbol": symbol,
            "type": "inconsistent_adj_open",
            "dates": bad_adj_open["Date"].tolist(),
        })

    # 6. Session coverage vs XNYS expected sessions
    date_set = set(dates)
    expected_set = set(expected_sessions)
    missing_sessions = sorted(expected_set - date_set)
    extra_sessions = sorted(date_set - expected_set)

    if missing_sessions:
        issues.append({
            "symbol": symbol,
            "type": "missing_sessions",
            "count": len(missing_sessions),
            "sessions": missing_sessions,
        })

    if extra_sessions:
        issues.append({
            "symbol": symbol,
            "type": "extra_sessions",
            "count": len(extra_sessions),
            "sessions": extra_sessions,
        })

    return issues


def np_is_inf(series: pd.Series) -> bool:
    """Check if any value in series is infinite."""
    return bool(((series == float("inf")) | (series == float("-inf"))).any())


def create_snapshot(
    source_dir: Path,
    target_dir: Path,
    cutoff: str = "2026-09-18",
    as_of: str = "2026-09-18T16:01:00-04:00",
    start_date: str = "2005-01-03",
) -> dict[str, Any]:
    """Create a frozen byte-level snapshot of the 8 ETF cache and XNYS calendar.

    Validates all files against expected XNYS sessions and produces manifest metadata.
    """
    target_dir.mkdir(parents=True, exist_ok=True)

    # 1. Build & freeze XNYS calendar session table
    session_df = build_session_table(start=start_date, end=cutoff)
    session_table_path = target_dir / "session_table.csv"
    session_df.to_csv(session_table_path, index=False)
    calendar_hash = sha256(session_table_path.read_bytes())

    expected_sessions = session_df["session_date"].tolist()

    # 2. Copy and verify each ETF
    etf_metadata: dict[str, Any] = {}
    validation_issues: list[dict[str, Any]] = []

    for sym in UNIVERSE:
        src = source_dir / f"{sym}.csv"
        if not src.is_file():
            raise DataValidationError(f"Missing source cache file for universe asset: {src}")

        dst = target_dir / f"{sym}.csv"
        file_hash = copy_verify(src, dst)

        df = pd.read_csv(dst)

        # Validate against cutoff
        if df["Date"].max() > cutoff:
            raise DataValidationError(f"{sym} contains records beyond cutoff {cutoff}")

        # Strict validation
        issues = validate_etf_series(df, sym, expected_sessions)
        if issues:
            validation_issues.extend(issues)

        etf_metadata[sym] = {
            "row_count": len(df),
            "first_date": str(df["Date"].iloc[0]),
            "last_date": str(df["Date"].iloc[-1]),
            "columns": list(df.columns),
            "sha256": file_hash,
            "source_path": str(src),
        }

    if validation_issues:
        raise DataValidationError(f"Data validation failed during snapshot: {validation_issues}")

    # 3. Build overall data hash
    combined_hashes = {sym: meta["sha256"] for sym, meta in sorted(etf_metadata.items())}
    data_sha256 = object_hash(combined_hashes)

    manifest = {
        "schema_version": 1,
        "dataset_id": target_dir.name,
        "as_of": as_of,
        "cutoff": cutoff,
        "start_date": start_date,
        "universe": list(UNIVERSE),
        "data_sha256": data_sha256,
        "calendar_sha256": calendar_hash,
        "expected_session_count": len(expected_sessions),
        "assets": etf_metadata,
        "status": "VALIDATED_PASS",
    }
    return manifest


def load_dataset(
    snapshot_dir: Path,
    partition: str,
    as_of: str,
    splits: Mapping[str, Any],
) -> DatasetView:
    """Load partition-scoped dataset view strictly preventing future lookahead."""
    as_of_dt = parse_as_of(as_of)
    as_of_date = as_of_dt.strftime("%Y-%m-%d")

    warmup_start = splits["warmup_start"]
    part_info = splits[partition]
    score_start = part_info["start"]
    score_end = part_info["end"]

    # Effective end date cannot exceed score_end or as_of_date
    effective_end = min(score_end, as_of_date)

    bars: dict[str, pd.DataFrame] = {}
    for sym in UNIVERSE:
        csv_path = snapshot_dir / f"{sym}.csv"
        df = pd.read_csv(csv_path)
        # Filter strictly within [warmup_start, effective_end]
        filtered = df[(df["Date"] >= warmup_start) & (df["Date"] <= effective_end)].copy()
        filtered.reset_index(drop=True, inplace=True)
        bars[sym] = filtered

    manifest_stub = DatasetManifest(
        dataset_id=snapshot_dir.name,
        data_sha256="",
        calendar_sha256="",
        protocol_sha256="",
        partitions={partition: {"start": score_start, "end": score_end}},
    )

    return DatasetView(
        manifest=manifest_stub,
        partition=partition,
        as_of=as_of,
        feature_start=warmup_start,
        score_start=score_start,
        score_end=score_end,
        bars=bars,
    )
