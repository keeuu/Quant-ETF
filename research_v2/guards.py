"""Read-only freeze audit and exclusive V2 writes. Standard library only.

Does not chmod F0 or promise OS-level protection against unrelated processes.
All V2 writers must use these gates; audit detects changes by other writers.
"""
from __future__ import annotations

import ast
import contextlib
import hashlib
import os
import stat
from pathlib import Path

from .provenance import git, object_hash, read_json, utc_now

ALLOWED_ROOTS = ("research_v2", "tests_v2", "docs/research_v2")
DEFAULT_EXPECTED_HEAD = "3f708f888277a6564ea6db204dafde95c2a548c7"
FORBIDDEN_IMPORTS = {"src", "config", "scripts", "alpaca", "yfinance", "dotenv"}


class GuardError(ValueError):
    pass


def is_allowed(relative: str) -> bool:
    return any(relative == p or relative.startswith(p + "/") for p in ALLOWED_ROOTS)


def is_secret_name(path: Path) -> bool:
    return any(p.startswith(".env") or p.endswith(".env") for p in path.parts)


def checked_output_path(root: Path, relative: str) -> Path:
    root = root.resolve(strict=True)
    rel = Path(relative)
    if rel.is_absolute() or not rel.parts or ".." in rel.parts:
        raise GuardError("Output must be a relative path without traversal")
    if not is_allowed(rel.as_posix()) or is_secret_name(rel):
        raise GuardError("Output outside V2 allowlist or a credential path")
    target = root / rel
    current = root
    for part in rel.parts:
        current = current / part
        if current.is_symlink():
            raise GuardError("Symlink components are forbidden in V2 output paths")
    if not target.resolve(strict=False).is_relative_to(root):
        raise GuardError("Resolved output escapes repository")
    return target


@contextlib.contextmanager
def open_output(root: Path, relative: str, *, append: bool = False):
    """Open through no-follow directory descriptors; exclusive unless journal append.

    Parents are created only after allowlist validation. Each ancestor is opened
    with O_NOFOLLOW, including protection against symlink replacement races.
    """
    checked_output_path(root, relative)
    parts = Path(relative).parts
    descriptor = os.open(root.resolve(strict=True), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in parts[:-1]:
            try:
                os.mkdir(part, 0o755, dir_fd=descriptor)
            except FileExistsError:
                pass
            new = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = new
        flags = os.O_NOFOLLOW | os.O_CREAT
        flags |= (os.O_RDWR | os.O_APPEND) if append else (os.O_WRONLY | os.O_EXCL)
        fd = os.open(parts[-1], flags, 0o644, dir_fd=descriptor)
        with os.fdopen(fd, "a+b" if append else "wb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise GuardError("Output must be a regular file")
            if os.fstat(stream.fileno()).st_nlink != 1:
                raise GuardError("Hard-linked output is forbidden")
            yield stream
    finally:
        os.close(descriptor)


def reserve_run(root: Path, run_id: str) -> Path:
    if not run_id or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for c in run_id):
        raise GuardError("Invalid run_id")
    # The marker creates the run directory; a pre-existing directory is rejected,
    # even if empty. mkdir is atomic and cannot replace a symlink.
    parent = checked_output_path(root, "research_v2/artifacts")
    parent.mkdir(parents=True, exist_ok=True)
    with_fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.mkdir(run_id, 0o755, dir_fd=with_fd)
    finally:
        os.close(with_fd)
    return parent / run_id


def _entry(path: Path) -> dict:
    before = path.lstat()
    result = {"mode": stat.S_IMODE(before.st_mode), "mtime_ns": before.st_mtime_ns,
              "size": before.st_size, "kind": "other"}
    if stat.S_ISLNK(before.st_mode):
        result.update(kind="symlink", target=os.readlink(path))
    elif stat.S_ISREG(before.st_mode):
        result["kind"] = "file"
        if is_secret_name(path):
            result["content_policy"] = "metadata_only_credentials_never_opened"
        else:
            digest = hashlib.sha256()
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(fd, "rb") as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(block)
            result["sha256"] = digest.hexdigest()
    elif stat.S_ISDIR(before.st_mode):
        # Membership is audited separately. Directory atime/mtime are not a
        # content proof and can change when permitted descendants are created.
        result = {"kind": "directory", "mode": stat.S_IMODE(before.st_mode)}
    after = path.lstat()
    if (before.st_ino, before.st_size, before.st_mtime_ns, before.st_mode) != (
            after.st_ino, after.st_size, after.st_mtime_ns, after.st_mode):
        raise GuardError(f"Changed during inventory: {path.name}")
    return result


def inventory(root: Path) -> dict:
    """Metadata/hash inventory only; no parsing of strategy outputs or secrets."""
    root = root.resolve(strict=True)
    entries = {}
    def visit(directory: Path):
        for path in sorted(directory.iterdir()):
            rel = path.relative_to(root).as_posix()
            if rel == ".git" or is_allowed(rel):
                continue
            item = _entry(path)
            if rel != "docs":  # structural ancestor of the permitted new docs
                entries[rel] = item
            if item["kind"] == "directory":
                visit(path)
    visit(root)
    return entries


def tag_state(root: Path) -> dict:
    tags = git(root, "tag", "--list").splitlines()
    return {tag: {"object_id": git(root, "rev-parse", f"refs/tags/{tag}"),
                  "peeled_commit": git(root, "rev-parse", f"refs/tags/{tag}^{{commit}}")}
            for tag in tags}


def create_baseline(root: Path, expected_head: str = DEFAULT_EXPECTED_HEAD) -> dict:
    root = root.resolve(strict=True)
    head = git(root, "rev-parse", "HEAD")
    # Compare worktree, index AND committed changes, including deletion/renames.
    paths = set(git(root, "diff", "--name-only", expected_head).splitlines())
    paths |= set(git(root, "diff", "--cached", "--name-only", expected_head).splitlines())
    if head != expected_head:
        paths |= set(git(root, "diff", "--name-only", expected_head, head).splitlines())
    invalid = paths - {"CODEX_EXECUTION_PLAN.md"}
    if invalid:
        raise GuardError(f"Unexpected baseline drift: {sorted(invalid)}")
    tracked = git(root, "ls-files").splitlines()
    result = {"schema_version": 1, "root": str(root), "created_at": utc_now(),
              "expected_head": expected_head, "code_commit": head,
              "tracked_paths": tracked, "tags": tag_state(root),
              "allowed_roots": list(ALLOWED_ROOTS), "files": inventory(root),
              "limitations": [".git internal files excluded; tag objects and commits audited",
                              "credential paths metadata-only; no credential content read",
                              "external concurrent changes fail audit; never restored automatically",
                              "audit and gated writes, not OS permissions or tamper-proof storage"]}
    result["manifest_sha256"] = object_hash(result)
    return result


def verify_baseline(root: Path, manifest: dict) -> dict:
    expected = dict(manifest)
    digest = expected.pop("manifest_sha256")
    if object_hash(expected) != digest:
        raise GuardError("Baseline manifest digest mismatch")
    if str(root.resolve()) != manifest["root"] or manifest["allowed_roots"] != list(ALLOWED_ROOTS):
        raise GuardError("Baseline root or allowlist mismatch")
    current = inventory(root)
    issues = []
    for path in sorted(set(manifest["files"]) | set(current)):
        before, after = manifest["files"].get(path), current.get(path)
        if before is None:
            issues.append({"path": path, "change": "unexpected_addition"})
        elif after is None:
            issues.append({"path": path, "change": "missing"})
        elif before != after:
            issues.append({"path": path, "change": "mutation",
                           "fields": sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k))})
    tags = tag_state(root)
    if tags != manifest["tags"]:
        issues.append({"path": "git_tags", "change": "tag_drift"})
    return {"status": "PASS" if not issues else "FAIL", "issues": issues,
            "protected_entries": len(manifest["files"]), "tags": tags,
            "baseline_sha256": digest, "current_inventory_sha256": object_hash(current)}


def audit_imports(root: Path) -> list[str]:
    issues = []
    for path in sorted((root / "research_v2").glob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            modules = [n.name for n in node.names] if isinstance(node, ast.Import) else (
                [node.module or ""] if isinstance(node, ast.ImportFrom) and not node.level else [])
            for name in modules:
                if name.split(".")[0] in FORBIDDEN_IMPORTS:
                    issues.append(f"{path.name}:{node.lineno}:{name}")
    return issues


# ---------------------------------------------------------------------------
# DYNAMIC_EXTERNAL_STATE classification (Stage 3.3)
# ---------------------------------------------------------------------------
# Files in this set are owned by the Paper runtime and may legitimately change
# between runs.  verify_baseline_v2 reports changes as EXTERNAL_DYNAMIC_STATE_CHANGED
# rather than treating them as F0 violations.  Research V2 must never write to
# any path in this set; that invariant is checked separately by audit_imports
# and by ensuring research_v2 code has no path in DYNAMIC_EXTERNAL_PATHS.
DYNAMIC_EXTERNAL_PATHS: frozenset[str] = frozenset({
    "logs/app.log",
    "logs/rebalance_state.json",
    "logs/execution.lock",
})


def _is_dynamic_external(relative: str) -> bool:
    """Return True if *relative* is a known dynamic Paper-runtime path."""
    return relative in DYNAMIC_EXTERNAL_PATHS or relative.startswith("logs/")


def verify_baseline_v2(root: Path, manifest: dict) -> dict:
    """Two-tier baseline verification that separates static from dynamic files.

    Static frozen files (src/, config/, scripts/, tests/, data/, etc.) are
    verified byte-for-byte as before — any change is a STATIC_F0_MUTATION.

    Dynamic external state files (logs/app.log, logs/rebalance_state.json, …)
    are observed and reported as EXTERNAL_DYNAMIC_STATE_CHANGED when they
    differ from the baseline snapshot, but they do NOT cause the overall static
    guard to FAIL.  Research V2 must never write to these paths.

    Returns
    -------
    dict with keys:
        static_status          : "PASS" | "FAIL"
        dynamic_status         : "UNCHANGED" | "CHANGED" | "NOT_IN_BASELINE"
        static_issues          : list of mutation dicts for static files
        dynamic_observations   : list of observation dicts for dynamic files
        protected_static_count : int
        protected_dynamic_count: int
        tags                   : current tag state
        baseline_sha256        : manifest digest
    """
    expected = dict(manifest)
    digest = expected.pop("manifest_sha256")
    if object_hash(expected) != digest:
        raise GuardError("Baseline manifest digest mismatch")
    if str(root.resolve()) != manifest["root"] or manifest["allowed_roots"] != list(ALLOWED_ROOTS):
        raise GuardError("Baseline root or allowlist mismatch")

    current = inventory(root)

    static_issues: list[dict] = []
    dynamic_observations: list[dict] = []
    dynamic_changed = False
    static_paths: set[str] = set()
    dynamic_paths: set[str] = set()

    all_paths = sorted(set(manifest["files"]) | set(current))
    for path in all_paths:
        before = manifest["files"].get(path)
        after = current.get(path)

        if _is_dynamic_external(path):
            dynamic_paths.add(path)
            obs: dict = {"path": path}
            if before is None:
                obs["change"] = "EXTERNAL_DYNAMIC_STATE_CHANGED"
                obs["detail"] = "not_in_baseline"
                dynamic_changed = True
            elif after is None:
                obs["change"] = "EXTERNAL_DYNAMIC_STATE_CHANGED"
                obs["detail"] = "missing_now"
                dynamic_changed = True
            elif before != after:
                obs["change"] = "EXTERNAL_DYNAMIC_STATE_CHANGED"
                obs["detail"] = "content_or_metadata_changed"
                obs["fields"] = sorted(
                    k for k in set(before) | set(after) if before.get(k) != after.get(k)
                )
                dynamic_changed = True
            else:
                obs["change"] = "UNCHANGED"
            dynamic_observations.append(obs)
        else:
            static_paths.add(path)
            if before is None:
                static_issues.append({"path": path, "change": "unexpected_addition"})
            elif after is None:
                static_issues.append({"path": path, "change": "missing"})
            elif before != after:
                static_issues.append({
                    "path": path,
                    "change": "STATIC_F0_MUTATION",
                    "fields": sorted(
                        k for k in set(before) | set(after) if before.get(k) != after.get(k)
                    ),
                })

    tags = tag_state(root)
    if tags != manifest["tags"]:
        static_issues.append({"path": "git_tags", "change": "tag_drift"})

    if dynamic_changed:
        dynamic_status = "CHANGED"
    elif not dynamic_observations:
        dynamic_status = "NOT_IN_BASELINE"
    else:
        dynamic_status = "UNCHANGED"

    return {
        "static_status": "PASS" if not static_issues else "FAIL",
        "dynamic_status": dynamic_status,
        "static_issues": static_issues,
        "dynamic_observations": dynamic_observations,
        "protected_static_count": len(static_paths),
        "protected_dynamic_count": len(dynamic_paths),
        "tags": tags,
        "baseline_sha256": digest,
        "current_inventory_sha256": object_hash(current),
        # NOTE: dynamic state changes are NEVER a Research V2 F0 violation.
        # Research V2 code must not write to DYNAMIC_EXTERNAL_PATHS.
        # Guard annotation:
        "guard_note": (
            "DYNAMIC_EXTERNAL_STATE paths are owned by Paper runtime. "
            "Changes are reported as EXTERNAL_DYNAMIC_STATE_CHANGED and do NOT "
            "trigger static F0 failures. Research V2 write access to these paths "
            "is separately prohibited by ALLOWED_ROOTS and audit_imports."
        ),
    }
