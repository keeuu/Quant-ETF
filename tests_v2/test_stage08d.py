import pytest
import json
import subprocess
import os
import pandas as pd
from pathlib import Path
from research_v2.provenance import object_hash, read_json
from research_v2.stage9_runner import verify_stage9_holdout_lock, stage9_holdout_once
from research_v2.contracts import Stage9HoldoutAuthorization, _STAGE9_CAPABILITY
from research_v2.data import load_dataset
import shutil
import uuid

root = Path(__file__).parent.parent
LOCK_REL = "docs/research_v2/stage_09_holdout_lock_v5.json"
EXPOSURE_REL = "docs/research_v2/holdout_exposure_state.json"
ACTIVE_REL = "research_v2/artifacts/phase_08/stage_09_holdout_lock_active_hash.txt"
lock_path = root / LOCK_REL  # read-only reference to the real lock (existence checks only)

# Files the Stage-9 runner reads/writes relative to its `root` argument.  They are
# copied into a temporary root so the tests can never delete or rewrite tracked files.
_COPY_FILES = [
    LOCK_REL,
    EXPOSURE_REL,
    ACTIVE_REL,
    "docs/research_v2/dataset_manifest.json",
    "research_v2/protocol.json",
    "research_v2/stage9_runner.py",
    "research_v2/data.py",
    "research_v2/contracts.py",
]


@pytest.fixture
def work_root(tmp_path):
    """Temporary copy of the minimal inputs the runner needs (never touches the repo tree)."""
    dest = tmp_path / "repo"
    for rel in _COPY_FILES:
        src = root / rel
        if src.exists():
            (dest / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest / rel)
    proto = dest / "research_v2/protocol.json"
    if proto.exists():
        pdata = json.loads(proto.read_text())
        if "protocol_path" in pdata and (root / pdata["protocol_path"]).exists():
            rel = pdata["protocol_path"]
            (dest / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(root / rel, dest / rel)
    # Read-only dataset snapshot: symlink instead of copying.
    snap_dst = dest / "research_v2/data/snapshot_20260918"
    snap_dst.parent.mkdir(parents=True, exist_ok=True)
    snap_dst.symlink_to(root / "research_v2/data/snapshot_20260918", target_is_directory=True)
    return dest


@pytest.fixture(autouse=True)
def clean_env(monkeypatch, work_root):
    exposure_path = work_root / EXPOSURE_REL
    if not exposure_path.exists():
        exposure_path.parent.mkdir(parents=True, exist_ok=True)
        exposure_path.write_text(json.dumps({"state": "UNEXPOSED"}))
    state = json.loads((work_root / EXPOSURE_REL).read_text())
    state["state"] = "UNEXPOSED"
    state["failed_unexposed_runs"] = 0
    state["successful_stage9_runs"] = 0
    state["retrospective_holdout_performance_accessed"] = False
    exposure_path.write_text(json.dumps(state))

    orig_run = subprocess.run
    def mock_run(*args, **kwargs):
        if args[0] == ["git", "status", "--porcelain"]:
            return type('obj', (object,), {'stdout': ''})
        if args[0] == ["git", "rev-parse", "HEAD"]:
            return type('obj', (object,), {'stdout': 'MOCK_HEAD'})
        if args[0] == ["git", "rev-parse", "research-v2-stage8-ready-v2^{commit}"]:
            return type('obj', (object,), {'stdout': 'MOCK_HEAD'})
        if args[0][:3] == ["git", "merge-base", "--is-ancestor"]:
            return type('obj', (object,), {'returncode': 0})
        return orig_run(*args, **kwargs)
    monkeypatch.setattr(subprocess, "run", mock_run)
    yield

def test_dirty_worktree(monkeypatch, work_root):
    orig_run = subprocess.run
    def mock_run(*args, **kwargs):
        if args[0] == ["git", "status", "--porcelain"]:
            return type('obj', (object,), {'stdout': 'M files'})
        return orig_run(*args, **kwargs)
    monkeypatch.setattr(subprocess, "run", mock_run)
    with pytest.raises(ValueError, match="FAIL_CLOSED_DIRTY_WORKTREE"):
        verify_stage9_holdout_lock(work_root / LOCK_REL, work_root)

def test_wrong_implementation_ancestry(monkeypatch, work_root):
    orig_run = subprocess.run
    def mock_run(*args, **kwargs):
        if args[0][:3] == ["git", "merge-base", "--is-ancestor"]:
            return type('obj', (object,), {'returncode': 1})
        if args[0] == ["git", "status", "--porcelain"]:
            return type('obj', (object,), {'stdout': ''})
        if args[0] == ["git", "rev-parse", "HEAD"]:
            return type('obj', (object,), {'stdout': 'MOCK_HEAD'})
        if args[0] == ["git", "rev-parse", "research-v2-stage8-ready-v2^{commit}"]:
            return type('obj', (object,), {'stdout': 'MOCK_HEAD'})
        return orig_run(*args, **kwargs)
    monkeypatch.setattr(subprocess, "run", mock_run)
    with pytest.raises(ValueError, match="FAIL_CLOSED_IMPLEMENTATION_NOT_ANCESTOR"):
        verify_stage9_holdout_lock(work_root / LOCK_REL, work_root)

def test_wrong_head_tag(monkeypatch, work_root):
    orig_run = subprocess.run
    def mock_run(*args, **kwargs):
        if args[0] == ["git", "rev-parse", "research-v2-stage8-ready-v2^{commit}"]:
            return type('obj', (object,), {'stdout': 'WRONG_HEAD'})
        if args[0] == ["git", "status", "--porcelain"]:
            return type('obj', (object,), {'stdout': ''})
        if args[0] == ["git", "rev-parse", "HEAD"]:
            return type('obj', (object,), {'stdout': 'MOCK_HEAD'})
        if args[0][:3] == ["git", "merge-base", "--is-ancestor"]:
            return type('obj', (object,), {'returncode': 0})
        return orig_run(*args, **kwargs)
    monkeypatch.setattr(subprocess, "run", mock_run)
    with pytest.raises(ValueError, match="FAIL_CLOSED_HEAD_NOT_READY_TAG"):
        verify_stage9_holdout_lock(work_root / LOCK_REL, work_root)

def test_code_manifest_mutation(tmp_path, work_root):
    if not lock_path.exists():
        pytest.skip("Lock v4 not generated yet")
    lock_data = json.loads((work_root / LOCK_REL).read_text())
    broken = dict(lock_data)
    del broken["lock_sha256"]
    broken["provenance"]["execution_code_manifest_sha256"] = "wrong_hash"
    broken["lock_sha256"] = object_hash(broken)
    temp_lock = tmp_path / "lock_wrong_manifest.json"
    temp_lock.write_text(json.dumps(broken))
    with pytest.raises(ValueError, match="Execution manifest mismatch"):
        verify_stage9_holdout_lock(temp_lock, work_root)

def test_generic_loader():
    with pytest.raises(ValueError, match="invalid or missing authorization"):
        load_dataset(root/"research_v2/data/snapshot_20260918", "holdout", "2026-09-18T16:01:00-04:00", {"warmup_start": "2005-01-03", "holdout": {"start":"2021-01-01", "end":"2026-09-18"}}, authorization=None)

def test_forged_authorization():
    auth = Stage9HoldoutAuthorization(
        lock_v4_hash="abcd",
        dataset_hash="65aa99046a92ae9b5fd80155cea26065d80ebcc78a740883452d2106e4000aa3",
        protocol_hash="abcd",
        execution_manifest_hash="abcd",
        _capability=object() # Fake capability
    )
    with pytest.raises(ValueError, match="invalid authorization capability"):
        load_dataset(root/"research_v2/data/snapshot_20260918", "holdout", "2026-09-18T16:01:00-04:00", {"warmup_start": "2005-01-03", "holdout": {"start":"2021-01-01", "end":"2026-09-18"}}, authorization=auth)

@pytest.fixture
def mock_engine(monkeypatch):
    def mock_load_dataset(*args, **kwargs):
        class MockDS:
            bars = pd.DataFrame()
        return MockDS()
    monkeypatch.setattr("research_v2.data.load_dataset", mock_load_dataset)
    
    def mock_run_backtest(ds, signals, cost, init):
        nav = pd.DataFrame({"nav": [100000]})
        tr = pd.DataFrame({"trades": []})
        pos = pd.DataFrame({"holdings": []})
        reb = pd.DataFrame({"rebalance": []})
        pend = pd.DataFrame({"pending": []})
        met = {"CAGR": 0.05}
        return nav, pos, tr, reb, pend, met
    monkeypatch.setattr("research_v2.engine.run_backtest", mock_run_backtest)
    
    monkeypatch.setattr("research_v2.signals.compute_model_signals_for_events", lambda *args, **kwargs: pd.DataFrame())
    monkeypatch.setattr("research_v2.signals.compute_matched_monthly_signals", lambda *args, **kwargs: pd.DataFrame())

def test_success_simulation(mock_engine, monkeypatch, work_root):
    if not lock_path.exists():
        pytest.skip("Lock v4 not generated yet")
        
    def mock_build_weekly(*args, **kwargs): return []
    def mock_build_monthly(*args, **kwargs): return []
    monkeypatch.setattr("research_v2.schedule.build_weekly_schedule", mock_build_weekly)
    monkeypatch.setattr("research_v2.schedule.build_monthly_matched_schedule", mock_build_monthly)
        
    stage9_holdout_once(str(work_root / LOCK_REL), str(work_root))
    
    final_dest = work_root / "research_v2/artifacts/phase_09"
    assert final_dest.exists()
    assert (final_dest / "holdout_trial_ledger.csv").exists()
    
    exposure = json.loads((work_root / EXPOSURE_REL).read_text())
    assert exposure["state"] == "COMPLETED_EXPOSED"
    assert exposure["retrospective_holdout_performance_accessed"] is True
    assert exposure["successful_stage9_runs"] == 1

def test_model_failure_simulation(mock_engine, monkeypatch, work_root):
    if not lock_path.exists():
        pytest.skip("Lock v4 not generated yet")
    
    def mock_build_weekly(*args, **kwargs): return []
    def mock_build_monthly(*args, **kwargs): return []
    monkeypatch.setattr("research_v2.schedule.build_weekly_schedule", mock_build_weekly)
    monkeypatch.setattr("research_v2.schedule.build_monthly_matched_schedule", mock_build_monthly)
    
    call_count = 0
    def fail_at_7(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 7:
            raise Exception("Mock failure at 7/18")
        nav = pd.DataFrame({"nav": [100000]})
        tr = pd.DataFrame({"trades": []})
        pos = pd.DataFrame({"holdings": []})
        reb = pd.DataFrame({"rebalance": []})
        pend = pd.DataFrame({"pending": []})
        met = {"CAGR": 0.05}
        return nav, pos, tr, reb, pend, met
        
    monkeypatch.setattr("research_v2.engine.run_backtest", fail_at_7)
    
    with pytest.raises(RuntimeError, match="Mock failure at 7/18"):
        stage9_holdout_once(str(work_root / LOCK_REL), str(work_root))
        
    final_dest = work_root / "research_v2/artifacts/phase_09"
    assert not final_dest.exists()
    
    stagings = list(final_dest.parent.glob(".phase_09_staging_*"))
    assert len(stagings) == 0
    
    exposure = json.loads((work_root / EXPOSURE_REL).read_text())
    assert exposure["state"] == "UNEXPOSED"
    assert exposure["retrospective_holdout_performance_accessed"] is False

def test_publish_failure_simulation(mock_engine, monkeypatch, work_root):
    if not lock_path.exists():
        pytest.skip("Lock v4 not generated yet")
        
    def mock_build_weekly(*args, **kwargs): return []
    def mock_build_monthly(*args, **kwargs): return []
    monkeypatch.setattr("research_v2.schedule.build_weekly_schedule", mock_build_weekly)
    monkeypatch.setattr("research_v2.schedule.build_monthly_matched_schedule", mock_build_monthly)
    
    def mock_replace(*args, **kwargs):
        raise OSError("Mock atomic rename failure")
    monkeypatch.setattr(os, "replace", mock_replace)
    
    with pytest.raises(RuntimeError, match="Mock atomic rename failure"):
        stage9_holdout_once(str(work_root / LOCK_REL), str(work_root))
        
    final_dest = work_root / "research_v2/artifacts/phase_09"
    assert not final_dest.exists()
    
    exposure = json.loads((work_root / EXPOSURE_REL).read_text())
    assert exposure["state"] == "UNEXPOSED"
    assert exposure["retrospective_holdout_performance_accessed"] is False

