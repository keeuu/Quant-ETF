"""Tests for data snapshotting, byte integrity, and strict ETF validation."""
from __future__ import annotations

from pathlib import Path
import pandas as pd
import pytest

from research_v2.data import (
    DataValidationError,
    copy_verify,
    load_dataset,
    validate_etf_series,
)
from research_v2.splits import PARTITIONS


def _make_valid_etf_df(dates: list[str]) -> pd.DataFrame:
    """Helper to generate a clean, valid price dataframe for given dates."""
    n = len(dates)
    data = {
        "Date": dates,
        "open": [100.0 + i for i in range(n)],
        "high": [105.0 + i for i in range(n)],
        "low": [95.0 + i for i in range(n)],
        "close": [102.0 + i for i in range(n)],
        "adj_close": [98.0 + i * 0.95 for i in range(n)],
        "adj_open": [(100.0 + i) * (98.0 + i * 0.95) / (102.0 + i) for i in range(n)],
        "volume": [1000000 + i * 100 for i in range(n)],
    }
    return pd.DataFrame(data)


def test_copy_verify_byte_integrity(tmp_path: Path):
    """Ensure copy_verify checks byte-level sha256 equivalence."""
    src = tmp_path / "source.csv"
    dst = tmp_path / "dst.csv"
    src.write_text("Date,close\n2026-09-18,100\n")

    h = copy_verify(src, dst)
    assert len(h) == 64
    assert dst.read_bytes() == src.read_bytes()


def test_validate_etf_series_clean():
    """Clean data passes validation with zero issues."""
    dates = ["2026-09-14", "2026-09-15", "2026-09-16", "2026-09-17", "2026-09-18"]
    df = _make_valid_etf_df(dates)
    issues = validate_etf_series(df, "SPY", expected_sessions=dates)
    assert issues == []


def test_validate_etf_series_missing_sessions():
    """Missing expected trading sessions detected and reported."""
    expected = ["2026-09-14", "2026-09-15", "2026-09-16", "2026-09-17", "2026-09-18"]
    # df is missing Wednesday 2026-09-16
    present = ["2026-09-14", "2026-09-15", "2026-09-17", "2026-09-18"]
    df = _make_valid_etf_df(present)

    issues = validate_etf_series(df, "QQQ", expected_sessions=expected)
    assert len(issues) == 1
    assert issues[0]["type"] == "missing_sessions"
    assert issues[0]["sessions"] == ["2026-09-16"]


def test_validate_etf_series_extra_sessions():
    """Extra dates not in expected sessions reported."""
    expected = ["2026-09-14", "2026-09-15", "2026-09-16"]
    dates = ["2026-09-14", "2026-09-15", "2026-09-16", "2026-09-20"]  # Sunday
    df = _make_valid_etf_df(dates)

    issues = validate_etf_series(df, "IWM", expected_sessions=expected)
    assert any(i["type"] == "extra_sessions" and "2026-09-20" in i["sessions"] for i in issues)


def test_validate_etf_series_duplicate_and_disorder():
    """Duplicate dates and non-monotonic date orders detected."""
    # Duplicate
    dates = ["2026-09-14", "2026-09-15", "2026-09-15", "2026-09-16"]
    df = _make_valid_etf_df(dates)
    issues = validate_etf_series(df, "GLD", expected_sessions=["2026-09-14", "2026-09-15", "2026-09-16"])
    assert any(i["type"] == "duplicate_dates" for i in issues)

    # Disorder
    dates = ["2026-09-15", "2026-09-14", "2026-09-16"]
    df = _make_valid_etf_df(dates)
    issues = validate_etf_series(df, "GLD", expected_sessions=["2026-09-14", "2026-09-15", "2026-09-16"])
    assert any(i["type"] == "dates_not_increasing" for i in issues)


def test_validate_etf_series_nan_inf_negative():
    """NaN, Inf, negative prices, and negative volume detected."""
    dates = ["2026-09-14", "2026-09-15"]

    # NaN in close
    df_nan = _make_valid_etf_df(dates)
    df_nan.loc[0, "close"] = float("nan")
    issues = validate_etf_series(df_nan, "TLT", expected_sessions=dates)
    assert any(i["type"] == "non_finite_values" for i in issues)

    # Inf in open
    df_inf = _make_valid_etf_df(dates)
    df_inf.loc[0, "open"] = float("inf")
    issues = validate_etf_series(df_inf, "TLT", expected_sessions=dates)
    assert any(i["type"] == "non_finite_values" for i in issues)

    # Negative price
    df_neg = _make_valid_etf_df(dates)
    df_neg.loc[0, "low"] = -5.0
    issues = validate_etf_series(df_neg, "TLT", expected_sessions=dates)
    assert any(i["type"] == "non_positive_price" for i in issues)

    # Negative volume
    df_vol = _make_valid_etf_df(dates)
    df_vol.loc[0, "volume"] = -100
    issues = validate_etf_series(df_vol, "TLT", expected_sessions=dates)
    assert any(i["type"] == "invalid_volume" for i in issues)


def test_validate_etf_series_illegal_ohlc():
    """High < Low, High < Open, or Low > Close detected."""
    dates = ["2026-09-14"]

    # High < Low
    df1 = _make_valid_etf_df(dates)
    df1.loc[0, "high"] = 90.0
    df1.loc[0, "low"] = 100.0
    issues1 = validate_etf_series(df1, "EFA", expected_sessions=dates)
    assert any(i["type"] == "high_less_than_low" for i in issues1)

    # High < Open
    df2 = _make_valid_etf_df(dates)
    df2.loc[0, "high"] = 99.0
    df2.loc[0, "open"] = 100.0
    issues2 = validate_etf_series(df2, "EFA", expected_sessions=dates)
    assert any(i["type"] == "high_less_than_open_close" for i in issues2)


def test_load_dataset_scoping(tmp_path: Path):
    """Ensure load_dataset strictly enforces partition boundaries and as_of."""
    snapshot_dir = tmp_path / "snapshot"
    snapshot_dir.mkdir()

    dates = ["2005-01-03", "2006-01-15", "2006-03-01", "2010-05-20", "2016-02-10", "2021-06-15"]
    for sym in ["SPY", "QQQ", "IWM", "EFA", "EEM", "GLD", "TLT", "IEF"]:
        df = _make_valid_etf_df(dates)
        df.to_csv(snapshot_dir / f"{sym}.csv", index=False)

    # Train partition: score_start=2006-02-01, score_end=2015-12-31, warmup_start=2005-01-03
    splits = {
        "warmup_start": "2005-01-03",
        "train": {"start": "2006-02-01", "end": "2015-12-31"},
    }
    view = load_dataset(snapshot_dir, partition="train", as_of="2026-09-18T16:01:00-04:00", splits=splits)

    spy_df = view.bars["SPY"]
    assert "2005-01-03" in spy_df["Date"].tolist(), "Warmup start must be included for indicator calculation"
    assert "2010-05-20" in spy_df["Date"].tolist(), "Within-train dates included"
    assert "2016-02-10" not in spy_df["Date"].tolist(), "Validation dates MUST NOT leak into train"
    assert "2021-06-15" not in spy_df["Date"].tolist(), "Holdout dates MUST NOT leak into train"
