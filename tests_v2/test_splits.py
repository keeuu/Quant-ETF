"""Tests for partition splits, Walk-Forward fold structures, and holdout gates."""
from __future__ import annotations

from pathlib import Path
import pytest

from research_v2.provenance import read_json
from research_v2.splits import (
    PARTITIONS,
    audit_splits,
    get_walk_forward_folds,
)


def test_splits_boundaries():
    """Verify partition boundary constants adhere to research protocol."""
    assert PARTITIONS["warmup"]["start"] == "2005-01-03"
    assert PARTITIONS["train"]["start"] == "2006-02-01"
    assert PARTITIONS["train"]["end"] == "2015-12-31"
    assert PARTITIONS["validation"]["start"] == "2016-01-01"
    assert PARTITIONS["validation"]["end"] == "2020-12-31"
    assert PARTITIONS["historical_holdout"]["start"] == "2021-01-01"
    assert PARTITIONS["historical_holdout"]["end"] == "2026-09-18"
    assert PARTITIONS["historical_holdout"]["label"] == "RETROSPECTIVE_HOLDOUT_EXPOSED_BY_V1"
    assert PARTITIONS["prospective_oos"]["status"] == "PENDING_DATA"


def test_walk_forward_folds_structure():
    """Verify the 10 annual chronological Walk-Forward folds."""
    folds = get_walk_forward_folds()
    assert len(folds) == 10

    years = [f["test_year"] for f in folds]
    assert years == list(range(2011, 2021))

    # Check first fold: 2011
    f2011 = folds[0]
    assert f2011["fold_id"] == "WF_2011"
    assert f2011["inner_train"]["start"] == "2006-02-01"
    assert f2011["inner_train"]["end"] == "2008-12-31"
    assert f2011["inner_validation"]["start"] == "2009-01-01"
    assert f2011["inner_validation"]["end"] == "2010-12-31"
    assert f2011["test"]["start"] == "2011-01-01"
    assert f2011["test"]["end"] == "2011-12-31"
    assert f2011["diagnostic_group"] == "train_internal"

    # Check last fold: 2020
    f2020 = folds[-1]
    assert f2020["fold_id"] == "WF_2020"
    assert f2020["inner_train"]["start"] == "2006-02-01"
    assert f2020["inner_train"]["end"] == "2017-12-31"
    assert f2020["inner_validation"]["start"] == "2018-01-01"
    assert f2020["inner_validation"]["end"] == "2019-12-31"
    assert f2020["test"]["start"] == "2020-01-01"
    assert f2020["test"]["end"] == "2020-12-31"
    assert f2020["diagnostic_group"] == "validation_time"


def test_audit_splits_metadata_only_gate(repo_root: Path):
    """Verify audit_splits enforces metadata-only mode and reports accurate sessions."""
    protocol = read_json(repo_root / "research_v2" / "protocol.json")

    # Mock sequence of trading session dates covering warmup to holdout
    dates = [
        "2005-01-03", "2005-06-15",
        "2006-02-01", "2010-05-10", "2015-12-31",
        "2016-01-04", "2018-03-20", "2020-12-31",
        "2021-01-04", "2024-07-15", "2026-09-18",
    ]

    report = audit_splits(protocol, session_dates=dates, metadata_only=True)
    assert report["status"] == "PASS"
    assert report["holdout_gate"]["status"] == "LOCKED_METADATA_ONLY"
    assert report["holdout_gate"]["strategy_returns_computed"] is False
    assert report["holdout_gate"]["holdout_unlocked"] is False

    # Metadata-only=False must be rejected
    with pytest.raises(ValueError, match="strictly permits metadata-only"):
        audit_splits(protocol, session_dates=dates, metadata_only=False)
