"""Deterministic, finite JSON and append-only evidence; no import-time I/O."""
from __future__ import annotations

import fcntl
import hashlib
import importlib.metadata
import json
import os
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def canonical_json(value: Any) -> bytes:
    """An explicit project format, not a claim of RFC 8785 compliance."""
    def check(item: Any) -> None:
        if isinstance(item, dict):
            if not all(isinstance(k, str) for k in item):
                raise ValueError("JSON object keys must be strings")
            for v in item.values():
                check(v)
        elif isinstance(item, (list, tuple)):
            for v in item:
                check(v)
    check(value)
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def object_hash(value: Any) -> str:
    return sha256(canonical_json(value))


def read_json(path: Path) -> Any:
    def pairs(values: list[tuple[str, Any]]) -> dict:
        result = {}
        for key, value in values:
            if key in result:
                raise ValueError(f"Duplicate JSON key: {key}")
            result[key] = value
        return result
    def invalid(value: str) -> None:
        raise ValueError(f"Non-finite JSON token: {value}")
    with path.open(encoding="utf-8") as stream:
        return json.load(stream, object_pairs_hook=pairs, parse_constant=invalid)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def git(root: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(root), *args],
                                   text=True, stderr=subprocess.PIPE).strip()


def runtime_metadata() -> dict:
    # Metadata only: never import third-party packages, inspect env, or read secrets.
    versions = {d.metadata["Name"].lower().replace("_", "-"): d.version
                for d in importlib.metadata.distributions() if d.metadata["Name"]}
    return {"python": sys.version, "executable": sys.executable,
            "prefix": sys.prefix, "base_prefix": sys.base_prefix,
            "platform": platform.platform(), "dependencies": dict(sorted(versions.items()))}


def write_json_new(root: Path, relative: str, value: Any) -> None:
    from .guards import open_output
    payload = canonical_json(value) + b"\n"
    with open_output(root, relative) as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())


class TrialRegistry:
    """Append event records with a hash chain and immutable trial specification.

    The caller must retain the returned head hash in a separate receipt. A hash
    chain detects editing and, with that anchor, truncation; it is not a defense
    against a privileged actor replacing both the journal and its anchor.
    """
    def __init__(self, root: Path, relative: str = "research_v2/artifacts/trial_registry.jsonl"):
        self.root, self.relative = root, relative

    @staticmethod
    def _parse(data: bytes, expected_head: str | None = None) -> list[dict]:
        records, previous, trials = [], "0" * 64, {}
        if data and not data.endswith(b"\n"):
            raise ValueError("Incomplete trial registry tail; preserve and investigate")
        for line in data.splitlines():
            record = json.loads(line)
            digest = record.pop("record_sha256")
            if (record["sequence"] != len(records) or record["previous_sha256"] != previous
                    or object_hash(record) != digest):
                raise ValueError("Trial registry chain mismatch")
            tid, event = record["trial_id"], record["event"]
            if event == "planned":
                if tid in trials:
                    raise ValueError("Duplicate trial")
                trials[tid] = {"spec_hash": record["spec_hash"], "terminal": False}
            elif event in {"succeeded", "failed", "abandoned"}:
                state = trials.get(tid)
                if state is None or state["terminal"] or state["spec_hash"] != record["spec_hash"]:
                    raise ValueError("Illegal trial transition")
                state["terminal"] = True
            else:
                raise ValueError("Unknown trial event")
            if object_hash(record["spec"]) != record["spec_hash"]:
                raise ValueError("Trial spec hash mismatch")
            record["record_sha256"] = digest
            records.append(record)
            previous = digest
        if expected_head is not None and previous != expected_head:
            raise ValueError("Registry head differs from receipt anchor")
        return records

    def read(self, expected_head: str | None = None) -> list[dict]:
        from .guards import checked_output_path
        path = checked_output_path(self.root, self.relative)
        return self._parse(path.read_bytes() if path.exists() else b"", expected_head)

    def append(self, trial_id: str, event: str, spec: dict, details: dict | None = None) -> str:
        from .guards import open_output
        if not trial_id or event not in {"planned", "succeeded", "failed", "abandoned"}:
            raise ValueError("Invalid trial id/event")
        # O_APPEND + flock: no rewrite/truncate path, concurrent writers serialize.
        with open_output(self.root, self.relative, append=True) as stream:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
            stream.seek(0)
            existing = stream.read()
            rows = self._parse(existing)
            record = {"sequence": len(rows), "previous_sha256": rows[-1]["record_sha256"] if rows else "0"*64,
                      "trial_id": trial_id, "event": event, "spec": spec,
                      "spec_hash": object_hash(spec), "details": details or {}, "recorded_at": utc_now()}
            record["record_sha256"] = object_hash(record)
            line = canonical_json(record) + b"\n"
            self._parse(existing + line)  # Validate transition before writing any bytes.
            stream.write(line)
            stream.flush()
            os.fsync(stream.fileno())
            return record["record_sha256"]
