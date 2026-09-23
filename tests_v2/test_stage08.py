import pytest
import json
from pathlib import Path
from research_v2.provenance import object_hash, sha256
from research_v2.data import load_dataset
import pandas as pd

root = Path(__file__).parent.parent
phase08 = root / "research_v2" / "artifacts" / "phase_08"

def test_cost_monotonicity():
    mono_path = phase08 / "cost_monotonicity_audit.json"
    assert mono_path.exists()
    mono = json.loads(mono_path.read_text())
    assert mono["status"] == "PASS", "Cost monotonicity failed"
    for v in mono["details"].values():
        assert v is True

def test_paired_bootstrap_determinism():
    bs_config_path = phase08 / "bootstrap_config.json"
    assert bs_config_path.exists()
    bs_config = json.loads(bs_config_path.read_text())
    assert bs_config["random_seed"] == 20260921
    assert bs_config["method"] == "paired_stationary_block"

def test_regime_label_determinism():
    reg_config_path = phase08 / "regime_config.json"
    assert reg_config_path.exists()
    reg_config = json.loads(reg_config_path.read_text())
    assert reg_config["instrument"] == "SPY"
    assert reg_config["trend_sma_sessions"] == 200

def test_stage9_lock_hash():
    lock_path = root / "docs" / "research_v2" / "stage_09_holdout_lock_v2.json"
    assert lock_path.exists()
    lock_data = json.loads(lock_path.read_text())
    assert "lock_sha256" in lock_data
    # Re-hash
    computed = dict(lock_data)
    del computed["lock_sha256"]
    assert object_hash(computed) == lock_data["lock_sha256"]

def test_holdout_firewall_active():
    from research_v2.provenance import read_json
    protocol = read_json(root / "docs/research_v2/protocol_lock.json")
    if "protocol_path" in protocol:
        protocol = read_json(root / protocol["protocol_path"])
    manifest = read_json(root / "docs/research_v2/dataset_manifest.json")
    snapshot_dir = root / "research_v2/data" / manifest["dataset_id"]
    
    with pytest.raises(ValueError, match="Holdout partition blocked"):
        load_dataset(snapshot_dir, "historical_holdout", "2026-09-18T16:00:00-05:00", protocol["splits"])

