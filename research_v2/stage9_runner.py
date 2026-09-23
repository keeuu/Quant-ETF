import json
import subprocess
import hashlib
import uuid
import os
import shutil
import pandas as pd
from pathlib import Path
from research_v2.provenance import object_hash, read_json
from research_v2.contracts import Stage9HoldoutAuthorization, _STAGE9_CAPABILITY
import traceback

def verify_stage9_holdout_lock(lock_path: Path, root: Path) -> Stage9HoldoutAuthorization:
    if not lock_path.exists():
        raise FileNotFoundError(f"Lock file not found: {lock_path}")
        
    lock_data = json.loads(lock_path.read_text())
    
    status_out = subprocess.run(["git", "status", "--porcelain"], cwd=root, capture_output=True, text=True).stdout.strip()
    if status_out:
        raise ValueError("FAIL_CLOSED_DIRTY_WORKTREE")
        
    head_commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True).stdout.strip()
    tag_commit = subprocess.run(["git", "rev-parse", "research-v2-stage8-ready^{commit}"], cwd=root, capture_output=True, text=True).stdout.strip()
    if head_commit != tag_commit or not tag_commit:
        raise ValueError("FAIL_CLOSED_HEAD_NOT_READY_TAG")
        
    impl_commit = lock_data["provenance"]["execution_implementation_commit"]
    ancestor_check = subprocess.run(["git", "merge-base", "--is-ancestor", impl_commit, "HEAD"], cwd=root)
    if ancestor_check.returncode != 0:
        raise ValueError("FAIL_CLOSED_IMPLEMENTATION_NOT_ANCESTOR")
        
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

    expected_hash = lock_data.pop("lock_sha256", None)
    if not expected_hash:
        raise ValueError("Missing lock_sha256 in lock file.")
    actual_hash = object_hash(lock_data)
    if actual_hash != expected_hash:
        raise ValueError("Holdout lock canonical hash mismatch.")
    lock_data["lock_sha256"] = expected_hash
    
    active_hash_path = root / "research_v2/artifacts/phase_08/stage_09_holdout_lock_active_hash.txt"
    if active_hash_path.read_text().strip() != actual_hash:
        raise ValueError("Lock v4 canonical hash does not match active locked hash.")

    manifest_path = root / "docs/research_v2/dataset_manifest.json"
    ds_manifest = read_json(manifest_path)
    if ds_manifest["data_sha256"] != lock_data["provenance"]["dataset_sha256"]:
        raise ValueError("Dataset hash mismatch.")
        
    protocol_path = root / lock_data["provenance"]["protocol_path"]
    protocol_data = read_json(protocol_path)
    if "protocol_path" in protocol_data:
        protocol_data = read_json(root / protocol_data["protocol_path"])
    actual_proto_hash = object_hash(protocol_data)
    if actual_proto_hash != lock_data["provenance"]["protocol_sha256"]:
        raise ValueError("Protocol hash mismatch.")
        
    if ds_manifest["calendar_sha256"] != lock_data["provenance"]["calendar_sha256"]:
        raise ValueError("Calendar hash mismatch.")

    pkg = lock_data["package"]
    if len(pkg["weekly_primary_models"]) != 9 or len(pkg["monthly_cadence_controls"]) != 9:
        raise ValueError("Model package count must be 18 (9 weekly, 9 monthly).")
        
    if lock_data["execution_contract"]["cost_bps"] != 5:
        raise ValueError("Cost must be 5 bps.")
        
    if lock_data["execution_contract"]["initial_wealth"] != 100000:
        raise ValueError("Initial wealth must be 100000.")
        
    expected_metrics = ["CAGR", "Vol", "Sharpe", "MaxDD", "Final NAV", "Total Fees", "Annualized One-Way Turnover", "Annualized Gross Traded Ratio", "Mean Cash", "Paired Cadence Deltas"]
    if sorted(lock_data["formal_metrics"]) != sorted(expected_metrics):
        raise ValueError("Formal metrics schema mismatch.")
        
    exposure_path = root / "docs/research_v2/holdout_exposure_state.json"
    exposure = json.loads(exposure_path.read_text())
    if exposure["state"] in ("EXPOSED", "COMPLETED_EXPOSED"):
        raise ValueError("Holdout has already been marked EXPOSED.")

    return Stage9HoldoutAuthorization(
        lock_v4_hash=actual_hash,
        dataset_hash=ds_manifest["data_sha256"],
        protocol_hash=actual_proto_hash,
        execution_manifest_hash=current_manifest_hash,
        _capability=_STAGE9_CAPABILITY
    )

def stage9_holdout_once(lock_path_str: str, root_str: str):
    root = Path(root_str)
    lock_path = Path(lock_path_str)
    
    auth = verify_stage9_holdout_lock(lock_path, root)
    lock_data = json.loads(lock_path.read_text())
    
    exposure_path = root / "docs/research_v2/holdout_exposure_state.json"
    exposure = json.loads(exposure_path.read_text())
    
    weekly_models = lock_data["package"]["weekly_primary_models"]
    monthly_models = lock_data["package"]["monthly_cadence_controls"]
    cost = lock_data["execution_contract"]["cost_bps"]
    initial_wealth = lock_data["execution_contract"]["initial_wealth"]
    
    final_dest = root / "research_v2/artifacts/phase_09"
    if final_dest.exists():
        raise RuntimeError(f"Target directory {final_dest} already exists.")
        
    uid = uuid.uuid4().hex
    staging_dir = final_dest.parent / f".phase_09_staging_{uid}"
    staging_dir.mkdir(parents=True, exist_ok=True)
    
    try:
        from research_v2.data import load_dataset
        ds = load_dataset(root / "research_v2/data/snapshot_20260918", "holdout", "2026-09-18T16:01:00-04:00", {"warmup_start": "2005-01-03", "holdout": {"start": "2021-01-01", "end": "2026-09-18"}}, auth)
        
        session_table_path = root / "research_v2/data/snapshot_20260918" / "session_table.csv"
        session_df = pd.read_csv(session_table_path)
        session_dates = session_df["session_date"].tolist()
        # In holdout, we build schedule up to 2026-09-18
        all_weekly = __import__("research_v2.schedule", fromlist=["build_weekly_schedule"]).build_weekly_schedule(session_dates, as_of="2026-09-18T16:01:00-04:00")
        all_monthly = __import__("research_v2.schedule", fromlist=["build_monthly_matched_schedule"]).build_monthly_matched_schedule(session_dates, as_of="2026-09-18T16:01:00-04:00", weekly_events=all_weekly)
        
        from research_v2.signals import compute_model_signals_for_events, compute_matched_monthly_signals
        from research_v2.engine import run_backtest
        from research_v2.metrics import compute_backtest_metrics
        
        all_metrics = []
        for i, m in enumerate(weekly_models):
            print(f"running {i+1}/18: {m}")
            parts = m.split("_")
            mom = int(parts[1][1:])
            trnd = int(parts[2][1:])
            
            w_sig = compute_model_signals_for_events(ds.bars, session_dates, all_weekly, m, mom, trnd)
            w_nav, w_pos, w_trd, w_reb, w_pend, w_met = run_backtest(ds, w_sig, cost, initial_wealth)
            w_nav.to_csv(staging_dir / f"{m}_nav.csv", index=False)
            w_trd.to_csv(staging_dir / f"{m}_trades.csv", index=False)
            
            w_met["model"] = m
            all_metrics.append(w_met)
            
            # Now run corresponding monthly model
            mm = monthly_models[i]
            print(f"running {i+1+9}/18: {mm}")
            m_sig = compute_matched_monthly_signals(w_sig, all_monthly, mm)
            m_nav, m_pos, m_trd, m_reb, m_pend, m_met = run_backtest(ds, m_sig, cost, initial_wealth)
            m_nav.to_csv(staging_dir / f"{mm}_nav.csv", index=False)
            m_trd.to_csv(staging_dir / f"{mm}_trades.csv", index=False)
            
            m_met["model"] = mm
            all_metrics.append(m_met)
            
        pd.DataFrame(all_metrics).to_csv(staging_dir / "holdout_trial_ledger.csv", index=False)
        
        # Exposure Commit
        exposure["retrospective_holdout_performance_accessed"] = True
        exposure["successful_stage9_runs"] += 1
        exposure["state"] = "COMPLETED_EXPOSED"
        exposure_path.write_text(json.dumps(exposure, indent=2))
        
        try:
            # Atomic Publish using standard Python atomic rename
            os.replace(staging_dir, final_dest)
        except Exception as e:
            # Revert exposure ledger
            exposure["retrospective_holdout_performance_accessed"] = False
            exposure["successful_stage9_runs"] -= 1
            exposure["state"] = "UNEXPOSED"
            exposure_path.write_text(json.dumps(exposure, indent=2))
            raise e
        
    except Exception as e:
        if staging_dir.exists():
            shutil.rmtree(staging_dir)
        exposure["failed_unexposed_runs"] += 1
        exposure_path.write_text(json.dumps(exposure, indent=2))
        raise RuntimeError(f"Holdout run failed and cleaned up staging: {e}")

