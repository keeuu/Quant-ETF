"""Guards, isolation, and sandbox tamper-detection tests."""
from __future__ import annotations

import os
import sys
from pathlib import Path
import pytest

from research_v2.guards import (
    ALLOWED_ROOTS,
    GuardError,
    audit_imports,
    checked_output_path,
    is_allowed,
    is_secret_name,
    open_output,
    reserve_run,
    verify_baseline,
)
from research_v2.provenance import (
    TrialRegistry,
    canonical_json,
    object_hash,
    read_json,
    sha256,
)


def test_v2_import_isolation(repo_root: Path):
    """Ensure importing research_v2 packages never loads src or config modules."""
    for mod in list(sys.modules):
        if mod.startswith("src") or mod.startswith("config") or mod.startswith("scripts"):
            del sys.modules[mod]

    import research_v2  # noqa: F401
    import research_v2.cli  # noqa: F401
    import research_v2.contracts  # noqa: F401
    import research_v2.guards  # noqa: F401
    import research_v2.provenance  # noqa: F401

    loaded_forbidden = [m for m in sys.modules if m.startswith(("src.", "config.", "scripts."))]
    assert not loaded_forbidden, f"Forbidden legacy modules loaded: {loaded_forbidden}"

    issues = audit_imports(repo_root)
    assert not issues, f"Forbidden imports detected in research_v2 AST: {issues}"


def test_checked_output_path_allowlist(repo_root: Path):
    """Ensure output paths are strictly within V2 allowlist and reject traversal/secrets."""
    # Valid paths
    p1 = checked_output_path(repo_root, "research_v2/artifacts/test.txt")
    assert p1 == repo_root / "research_v2/artifacts/test.txt"

    p2 = checked_output_path(repo_root, "tests_v2/fixtures/toy.csv")
    assert p2 == repo_root / "tests_v2/fixtures/toy.csv"

    p3 = checked_output_path(repo_root, "docs/research_v2/report.md")
    assert p3 == repo_root / "docs/research_v2/report.md"

    # Absolute path rejected
    with pytest.raises(GuardError, match="must be a relative path"):
        checked_output_path(repo_root, "/etc/passwd")

    # Traversal rejected
    with pytest.raises(GuardError, match="without traversal"):
        checked_output_path(repo_root, "research_v2/../src/evil.py")

    # Disallowed roots rejected
    with pytest.raises(GuardError, match="outside V2 allowlist"):
        checked_output_path(repo_root, "src/data/hack.py")

    with pytest.raises(GuardError, match="outside V2 allowlist"):
        checked_output_path(repo_root, "config/hack.py")

    with pytest.raises(GuardError, match="outside V2 allowlist"):
        checked_output_path(repo_root, "data/cache/hack.csv")

    # Secrets rejected
    with pytest.raises(GuardError, match="credential path"):
        checked_output_path(repo_root, "research_v2/.env")

    with pytest.raises(GuardError, match="credential path"):
        checked_output_path(repo_root, "research_v2/sub/.env.secret")


def test_open_output_exclusive_and_atomic(tmp_path: Path):
    """Test safe file creation, directory parent creation, and exclusive write locking."""
    # Create fake repo structure inside tmp_path
    v2_root = tmp_path / "research_v2"
    v2_root.mkdir()

    target_rel = "research_v2/sub/output.json"
    with open_output(tmp_path, target_rel) as stream:
        stream.write(b'{"status":"ok"}')

    created = tmp_path / target_rel
    assert created.exists()
    assert created.read_text() == '{"status":"ok"}'

    # Exclusive write: opening again without append=True must fail (FileExistsError or GuardError)
    with pytest.raises((FileExistsError, OSError)):
        with open_output(tmp_path, target_rel):
            pass

    # Append mode allows writing additional data
    with open_output(tmp_path, target_rel, append=True) as stream:
        stream.write(b'\n{"status":"second"}')

    assert created.read_text() == '{"status":"ok"}\n{"status":"second"}'


def test_reserve_run(tmp_path: Path):
    """Test run directory reservation and duplicate prevention."""
    artifacts = tmp_path / "research_v2" / "artifacts"
    artifacts.mkdir(parents=True)

    # Valid reservation
    run_dir = reserve_run(tmp_path, "run_001")
    assert run_dir == artifacts / "run_001"
    assert run_dir.is_dir()

    # Duplicate reservation fails
    with pytest.raises(FileExistsError):
        reserve_run(tmp_path, "run_001")

    # Invalid run_id fails
    with pytest.raises(GuardError, match="Invalid run_id"):
        reserve_run(tmp_path, "run/evil")

    with pytest.raises(GuardError, match="Invalid run_id"):
        reserve_run(tmp_path, "run..name")


def test_verify_baseline_in_sandbox(tmp_path: Path):
    """Test tamper detection in an isolated mock repository."""
    # Setup mock structure
    mock_src = tmp_path / "src"
    mock_src.mkdir()
    f1 = mock_src / "code.py"
    f1.write_text("print(1)")

    f1_stat = f1.lstat()
    manifest = {
        "schema_version": 1,
        "root": str(tmp_path.resolve()),
        "created_at": "2026-09-21T00:00:00+00:00",
        "expected_head": "000000",
        "code_commit": "000000",
        "tracked_paths": ["src/code.py"],
        "tags": {"test-tag": {"object_id": "aaa", "peeled_commit": "aaa"}},
        "allowed_roots": list(ALLOWED_ROOTS),
        "files": {
            "src": {"kind": "directory", "mode": f1.parent.lstat().st_mode & 0o777},
            "src/code.py": {
                "kind": "file",
                "mode": f1_stat.st_mode & 0o777,
                "mtime_ns": f1_stat.st_mtime_ns,
                "size": f1_stat.st_size,
                "sha256": sha256(b"print(1)"),
            },
        },
        "limitations": [],
    }
    manifest["manifest_sha256"] = object_hash(manifest)

    # Patch git call inside tag_state for mock
    import research_v2.guards as guards
    original_tag_state = guards.tag_state
    try:
        guards.tag_state = lambda r: {"test-tag": {"object_id": "aaa", "peeled_commit": "aaa"}}

        # Untampered: PASS
        res = verify_baseline(tmp_path, manifest)
        assert res["status"] == "PASS"
        assert res["issues"] == []

        # Tampered content: Mutation detected
        f1.write_text("print(2)")
        res = verify_baseline(tmp_path, manifest)
        assert res["status"] == "FAIL"
        assert any(i["change"] == "mutation" for i in res["issues"])

        # Restored content
        f1.write_text("print(1)")
        # Align mtime
        os.utime(f1, ns=(f1_stat.st_atime_ns, f1_stat.st_mtime_ns))

        # Deleted file: Missing detected
        f1.unlink()
        res = verify_baseline(tmp_path, manifest)
        assert res["status"] == "FAIL"
        assert any(i["change"] == "missing" for i in res["issues"])

        # Restored
        f1.write_text("print(1)")
        os.utime(f1, ns=(f1_stat.st_atime_ns, f1_stat.st_mtime_ns))

        # Unexpected file: Addition detected
        unauth = mock_src / "extra.py"
        unauth.write_text("extra")
        res = verify_baseline(tmp_path, manifest)
        assert res["status"] == "FAIL"
        assert any(i["change"] == "unexpected_addition" for i in res["issues"])
        unauth.unlink()

        # Tag drift detected
        guards.tag_state = lambda r: {"test-tag": {"object_id": "bbb", "peeled_commit": "bbb"}}
        res = verify_baseline(tmp_path, manifest)
        assert res["status"] == "FAIL"
        assert any(i["change"] == "tag_drift" for i in res["issues"])

    finally:
        guards.tag_state = original_tag_state


def test_provenance_canonical_json_and_trial_registry(tmp_path: Path):
    """Test canonical JSON determinism and append-only trial journal with hash chain."""
    # Deterministic JSON serialization
    d1 = {"b": 2, "a": 1}
    d2 = {"a": 1, "b": 2}
    assert canonical_json(d1) == canonical_json(d2)
    assert object_hash(d1) == object_hash(d2)

    # Non-finite values rejected
    with pytest.raises(ValueError):
        canonical_json({"inf": float("inf")})

    with pytest.raises(ValueError):
        canonical_json({"nan": float("nan")})

    # Trial Registry
    v2_root = tmp_path / "research_v2" / "artifacts"
    v2_root.mkdir(parents=True)

    reg = TrialRegistry(tmp_path, "research_v2/artifacts/trials.jsonl")
    records = reg.read()
    assert records == []

    # Append planned
    spec = {"model_id": "W_M06_T36", "seed": 42}
    h1 = reg.append("trial_1", "planned", spec)
    assert len(h1) == 64

    # Duplicate planned rejected
    with pytest.raises(ValueError, match="Duplicate trial"):
        reg.append("trial_1", "planned", spec)

    # Append succeeded
    h2 = reg.append("trial_1", "succeeded", spec, {"sharpe": 1.25})
    assert len(h2) == 64
    assert h2 != h1

    # Transition after terminal rejected
    with pytest.raises(ValueError, match="Illegal trial transition"):
        reg.append("trial_1", "failed", spec)

    # Verify chain read
    records = reg.read(expected_head=h2)
    assert len(records) == 2
    assert records[0]["event"] == "planned"
    assert records[1]["event"] == "succeeded"
    assert records[1]["previous_sha256"] == records[0]["record_sha256"]
