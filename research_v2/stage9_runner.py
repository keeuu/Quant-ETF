import json
import subprocess
import hashlib
from pathlib import Path
from research_v2.provenance import object_hash, read_json
from research_v2.contracts import Stage9HoldoutAuthorization
import sys
import tempfile
import shutil

def verify_stage9_holdout_lock(lock_path: Path, root: Path) -> Stage9HoldoutAuthorization:
    if not lock_path.exists():
        raise FileNotFoundError(f"Lock file not found: {lock_path}")
        
    lock_data = json.loads(lock_path.read_text())
    
    # 1. Verify worktree clean
    status_out = subprocess.run(["git", "status", "--porcelain"], cwd=root, capture_output=True, text=True).stdout.strip()
    if status_out:
        raise ValueError("FAIL_CLOSED_DIRTY_WORKTREE")
        
    # 2. HEAD == research-v2-stage8-ready
    head_commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True).stdout.strip()
    tag_commit = subprocess.run(["git", "rev-parse", "research-v2-stage8-ready^{commit}"], cwd=root, capture_output=True, text=True).stdout.strip()
    if head_commit != tag_commit or not tag_commit:
        raise ValueError("FAIL_CLOSED_HEAD_NOT_READY_TAG")
        
    # 3. execution implementation commit is ancestor of HEAD
    impl_commit = lock_data["provenance"]["execution_implementation_commit"]
    ancestor_check = subprocess.run(["git", "merge-base", "--is-ancestor", impl_commit, "HEAD"], cwd=root)
    if ancestor_check.returncode != 0:
        raise ValueError("FAIL_CLOSED_IMPLEMENTATION_NOT_ANCESTOR")
        
    # 4. current execution-critical file manifest == locked execution manifest
    # Re-compute manifest from current working tree
    critical_paths = [
        "research_v2/stage9_runner.py",
        "research_v2/data.py",
        "research_v2/contracts.py"
    ]
    manifest = {}
    for p in critical_paths:
        full_p = root / p
        if not full_p.exists():
            raise ValueError(f"Critical path missing: {p}")
        h = hashlib.sha256(full_p.read_bytes()).hexdigest()
        blob_sha = subprocess.run(["git", "hash-object", str(full_p)], capture_output=True, text=True).stdout.strip()
        manifest[p] = {"path": p, "git_blob_sha": blob_sha, "sha256": h}
    
    current_manifest_hash = object_hash(manifest)
    if current_manifest_hash != lock_data["provenance"]["execution_code_manifest_sha256"]:
        raise ValueError("Execution manifest mismatch.")

    # 5. Lock v3 canonical hash == active locked hash
    expected_hash = lock_data.pop("lock_sha256", None)
    if not expected_hash:
        raise ValueError("Missing lock_sha256 in lock file.")
    actual_hash = object_hash(lock_data)
    if actual_hash != expected_hash:
        raise ValueError("Holdout lock canonical hash mismatch.")
    lock_data["lock_sha256"] = expected_hash
    
    active_hash_path = root / "research_v2/artifacts/phase_08/stage_09_holdout_lock_active_hash.txt"
    if active_hash_path.read_text().strip() != actual_hash:
        raise ValueError("Lock v3 canonical hash does not match active locked hash.")

    # 6. dataset hash
    manifest_path = root / "docs/research_v2/dataset_manifest.json"
    ds_manifest = read_json(manifest_path)
    if ds_manifest["data_sha256"] != lock_data["provenance"]["dataset_sha256"]:
        raise ValueError("Dataset hash mismatch.")
        
    # 7. protocol hash
    protocol_path = root / lock_data["provenance"]["protocol_path"]
    protocol_data = read_json(protocol_path)
    if "protocol_path" in protocol_data: # handle protocol_lock.json nesting
        protocol_data = read_json(root / protocol_data["protocol_path"])
    actual_proto_hash = object_hash(protocol_data)
    if actual_proto_hash != lock_data["provenance"]["protocol_sha256"]:
        raise ValueError("Protocol hash mismatch.")
        
    # 8. calendar hash
    if ds_manifest["calendar_sha256"] != lock_data["provenance"]["calendar_sha256"]:
        raise ValueError("Calendar hash mismatch.")

    # 9. exact 18-model package
    pkg = lock_data["package"]
    if len(pkg["weekly_primary_models"]) != 9 or len(pkg["monthly_cadence_controls"]) != 9:
        raise ValueError("Model package count must be 18 (9 weekly, 9 monthly).")
        
    # 10. cost = 5
    if lock_data["execution_contract"]["cost_bps"] != 5:
        raise ValueError("Cost must be 5 bps.")
        
    # 11. initial wealth = 100000
    if lock_data["execution_contract"]["initial_wealth"] != 100000:
        raise ValueError("Initial wealth must be 100000.")
        
    # 12. formal metric schema
    expected_metrics = ["CAGR", "Vol", "Sharpe", "MaxDD", "Final NAV", "Total Fees", "Annualized One-Way Turnover", "Annualized Gross Traded Ratio", "Mean Cash", "Paired Cadence Deltas"]
    if sorted(lock_data["formal_metrics"]) != sorted(expected_metrics):
        raise ValueError("Formal metrics schema mismatch.")
        
    # 13. exposure state permits run
    exposure_path = root / "docs/research_v2/holdout_exposure_state.json"
    exposure = json.loads(exposure_path.read_text())
    if exposure["state"] in ("EXPOSED", "COMPLETED_EXPOSED"):
        raise ValueError("Holdout has already been marked EXPOSED.")

    # 14. create Stage9HoldoutAuthorization
    sig = hashlib.sha256(f"{actual_hash}:{ds_manifest['data_sha256']}:{actual_proto_hash}:{current_manifest_hash}:STAGE9_SEAL".encode()).hexdigest()
    return Stage9HoldoutAuthorization(
        lock_v3_hash=actual_hash,
        dataset_hash=ds_manifest["data_sha256"],
        protocol_hash=actual_proto_hash,
        execution_manifest_hash=current_manifest_hash,
        signature=sig
    )

def simulate_stage9_execution(lock_path: Path, root: Path, fail_at: int = -1):
    auth = verify_stage9_holdout_lock(lock_path, root)
    lock_data = json.loads(lock_path.read_text())
    
    models = lock_data["package"]["weekly_primary_models"] + lock_data["package"]["monthly_cadence_controls"]
    if len(models) != 18:
        raise ValueError("Must be exactly 18 models.")
    
    final_dest = root / "research_v2/artifacts/phase_09"
    
    exposure_path = root / "docs/research_v2/holdout_exposure_state.json"
    exposure = json.loads(exposure_path.read_text())
    
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        
        for i, m in enumerate(models):
            print(f"running {i+1}/18")
            if i == fail_at:
                exposure["failed_unexposed_runs"] += 1
                exposure_path.write_text(json.dumps(exposure, indent=2))
                raise RuntimeError(f"Mock failure at model {m}")
                
            (temp_path / f"{m}_nav.csv").write_text("date,nav\n2021-01-01,100000\n")
            
        final_dest.mkdir(parents=True, exist_ok=True)
        for f in temp_path.glob("*"):
            shutil.copy(f, final_dest / f.name)
            
        exposure["successful_stage9_runs"] += 1
        exposure["state"] = "COMPLETED_EXPOSED"
        exposure_path.write_text(json.dumps(exposure, indent=2))

def stage9_holdout_once(lock_path_str: str, root_str: str):
    root = Path(root_str)
    lock_path = Path(lock_path_str)
    
    auth = verify_stage9_holdout_lock(lock_path, root)
    print("Verification passed. Authorization created.")
