"""Research Protocol registration and lock verification tests."""
from __future__ import annotations

import copy
from pathlib import Path
import pytest

from research_v2.contracts import (
    EXPECTED_PLAN_SHA256,
    EXPECTED_PROTOCOL_SHA256,
    UNIVERSE,
    validate_protocol,
)
from research_v2.provenance import object_hash, read_json, sha256


def test_protocol_constants_and_validation(repo_root: Path):
    """Validate protocol.json strictly adheres to pre-registered research definition."""
    protocol_path = repo_root / "research_v2" / "protocol.json"
    assert protocol_path.is_file(), "protocol.json must exist"

    protocol = read_json(protocol_path)

    # Validate against expected digest anchor
    digest = validate_protocol(protocol)
    assert digest == EXPECTED_PROTOCOL_SHA256

    # Universe
    assert protocol["universe"] == list(UNIVERSE)
    assert protocol["universe"] == ["SPY", "QQQ", "IWM", "EFA", "EEM", "GLD", "TLT", "IEF"]

    # Model grid: exactly 9 models
    models = protocol["model_grid"]["models"]
    assert len(models) == 9
    expected_ids = [
        f"W_M{m:02d}_T{t}"
        for m in (6, 9, 12)
        for t in (36, 40, 44)
    ]
    actual_ids = [m["model_id"] for m in models]
    assert actual_ids == expected_ids
    assert len(set(actual_ids)) == 9

    # Portfolio constraints
    p = protocol["portfolio"]
    assert p["top_n"] == 3
    assert p["long_only"] is True
    assert p["leverage_allowed"] is False
    assert p["short_allowed"] is False
    assert p["initial_capital"] == 100000.0
    assert p["cash_return"] == 0.0

    # Costs
    assert protocol["costs"]["bps_per_side"] == [0, 5, 10, 20, 30, 50]
    assert protocol["costs"]["primary_bps"] == 5

    # Splits
    splits = protocol["splits"]
    assert splits["warmup_start"] == "2005-01-03"
    assert splits["train"]["start"] == "2006-02-01"
    assert splits["train"]["end"] == "2015-12-31"
    assert splits["validation"]["start"] == "2016-01-01"
    assert splits["validation"]["end"] == "2020-12-31"
    assert splits["historical_holdout"]["start"] == "2021-01-01"
    assert splits["historical_holdout"]["end"] == "2026-09-18"
    assert splits["historical_holdout"]["label"] == "RETROSPECTIVE_HOLDOUT_EXPOSED_BY_V1"


def test_protocol_tamper_rejection(repo_root: Path):
    """Ensure any parameter alteration is rejected by validate_protocol."""
    protocol = read_json(repo_root / "research_v2" / "protocol.json")

    # Tamper universe
    tampered = copy.deepcopy(protocol)
    tampered["universe"].append("AGG")
    with pytest.raises(ValueError, match="Universe/order differs"):
        validate_protocol(tampered)

    # Tamper models
    tampered = copy.deepcopy(protocol)
    tampered["model_grid"]["models"].pop()
    with pytest.raises(ValueError, match="Model grid must be the fixed nine unique models"):
        validate_protocol(tampered)

    # Tamper portfolio constraints
    tampered = copy.deepcopy(protocol)
    tampered["portfolio"]["top_n"] = 4
    with pytest.raises(ValueError, match="Long-only unlevered Top 3 constraint changed"):
        validate_protocol(tampered)

    tampered = copy.deepcopy(protocol)
    tampered["portfolio"]["leverage_allowed"] = True
    with pytest.raises(ValueError, match="Long-only unlevered Top 3 constraint changed"):
        validate_protocol(tampered)

    # Tamper subtle field
    tampered = copy.deepcopy(protocol)
    tampered["costs"]["primary_bps"] = 10
    with pytest.raises(ValueError, match="Protocol differs from the reviewed execution contract"):
        validate_protocol(tampered)


def test_protocol_lock_file(repo_root: Path):
    """Ensure protocol_lock.json exists, is valid, and matches execution plan."""
    lock_path = repo_root / "docs" / "research_v2" / "protocol_lock.json"
    assert lock_path.is_file(), "protocol_lock.json must exist"

    lock = read_json(lock_path)
    assert lock["status"] == "LOCKED"
    assert lock["protocol_sha256"] == EXPECTED_PROTOCOL_SHA256
    assert lock["model_count"] == 9
    assert lock["historical_strategy_returns_computed"] is False
    assert lock["holdout_returns_viewed"] is False

    # Execution plan hash match
    plan_path = repo_root / "CODEX_EXECUTION_PLAN.md"
    assert plan_path.is_file()
    assert sha256(plan_path.read_bytes()) == EXPECTED_PLAN_SHA256
    assert lock["execution_plan_sha256"] == EXPECTED_PLAN_SHA256

    # Baseline manifest match
    baseline_path = repo_root / "docs" / "research_v2" / "baseline_manifest.json"
    assert baseline_path.is_file()
    baseline = read_json(baseline_path)
    assert lock["baseline_manifest_sha256"] == baseline["manifest_sha256"]
