import pytest
import json
import subprocess
from pathlib import Path
from research_v2.provenance import object_hash, read_json
from research_v2.stage9_runner import verify_stage9_holdout_lock, simulate_stage9_execution
from research_v2.contracts import Stage9HoldoutAuthorization
from research_v2.data import load_dataset
import shutil

root = Path(__file__).parent.parent
lock_path = root / "docs/research_v2/stage_09_holdout_lock_v3.json"
exposure_path = root / "docs/research_v2/holdout_exposure_state.json"

@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    if not exposure_path.exists():
        exposure_path.write_text(json.dumps({"state": "UNEXPOSED"}))
    state = json.loads(exposure_path.read_text())
    orig_state = dict(state)
    state["state"] = "UNEXPOSED"
    exposure_path.write_text(json.dumps(state))
    
    orig_run = subprocess.run
    def mock_run(*args, **kwargs):
        if args[0] == ["git", "status", "--porcelain"]:
            return type('obj', (object,), {'stdout': ''})
        if args[0] == ["git", "rev-parse", "HEAD"]:
            return type('obj', (object,), {'stdout': 'MOCK_HEAD'})
        if args[0] == ["git", "rev-parse", "research-v2-stage8-ready^{commit}"]:
            return type('obj', (object,), {'stdout': 'MOCK_HEAD'})
        if args[0][:3] == ["git", "merge-base", "--is-ancestor"]:
            return type('obj', (object,), {'returncode': 0})
        if args[0][:2] == ["git", "hash-object"]:
            return type('obj', (object,), {'stdout': 'MOCK_BLOB_SHA'})
        return orig_run(*args, **kwargs)
    monkeypatch.setattr(subprocess, "run", mock_run)
    
    yield
    exposure_path.write_text(json.dumps(orig_state))

def test_missing_lock_keys(tmp_path):
    lock_data = json.loads(lock_path.read_text())
    for key in ["provenance", "package", "execution_contract"]:
        temp_lock = tmp_path / f"lock_missing_{key}.json"
        broken = dict(lock_data)
        if "lock_sha256" in broken:
            del broken["lock_sha256"]
        if key in broken:
            del broken[key]
        broken["lock_sha256"] = object_hash(broken)
        temp_lock.write_text(json.dumps(broken))
        with pytest.raises(KeyError):
            verify_stage9_holdout_lock(temp_lock, root)

def test_wrong_implementation_ancestry(tmp_path, monkeypatch):
    import subprocess
    orig_run = subprocess.run
    def mock_run(*args, **kwargs):
        if args[0][:3] == ["git", "merge-base", "--is-ancestor"]:
            return type('obj', (object,), {'returncode': 1})
        if args[0] == ["git", "status", "--porcelain"]:
            return type('obj', (object,), {'stdout': ''})
        if args[0] == ["git", "rev-parse", "HEAD"]:
            return type('obj', (object,), {'stdout': 'MOCK_HEAD'})
        if args[0] == ["git", "rev-parse", "research-v2-stage8-ready^{commit}"]:
            return type('obj', (object,), {'stdout': 'MOCK_HEAD'})
        return orig_run(*args, **kwargs)
    monkeypatch.setattr(subprocess, "run", mock_run)
    with pytest.raises(ValueError, match="FAIL_CLOSED_IMPLEMENTATION_NOT_ANCESTOR"):
        verify_stage9_holdout_lock(lock_path, root)

def test_wrong_head_tag(tmp_path, monkeypatch):
    import subprocess
    orig_run = subprocess.run
    def mock_run(*args, **kwargs):
        if args[0] == ["git", "rev-parse", "research-v2-stage8-ready^{commit}"]:
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
        verify_stage9_holdout_lock(lock_path, root)

def test_code_manifest_mutation(tmp_path):
    lock_data = json.loads(lock_path.read_text())
    broken = dict(lock_data)
    del broken["lock_sha256"]
    broken["provenance"]["execution_code_manifest_sha256"] = "wrong_hash"
    broken["lock_sha256"] = object_hash(broken)
    temp_lock = tmp_path / "lock_wrong_manifest.json"
    temp_lock.write_text(json.dumps(broken))
    with pytest.raises(ValueError, match="Execution manifest mismatch"):
        verify_stage9_holdout_lock(temp_lock, root)

def test_generic_loader():
    with pytest.raises(ValueError, match="invalid or missing authorization"):
        load_dataset(root/"research_v2/data/snapshot_20260918", "holdout", "2026-09-18T16:01:00-04:00", {"warmup_start": "2005-01-03", "holdout": {"start":"2021-01-01", "end":"2026-09-18"}})

def test_atomic_publishing(tmp_path, capsys):
    final_dest = root / "research_v2/artifacts/phase_09"
    if final_dest.exists():
        shutil.rmtree(final_dest)
        
    with pytest.raises(RuntimeError, match="Mock failure at model W_M12_T36"):
        simulate_stage9_execution(lock_path, root, fail_at=6)
        
    assert not final_dest.exists() or len(list(final_dest.glob("*"))) == 0
    
    captured = capsys.readouterr()
    assert "CAGR" not in captured.out
    assert "running 1/18" in captured.out
    assert "running 7/18" in captured.out

