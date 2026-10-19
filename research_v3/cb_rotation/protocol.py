"""Loads protocol_cb_rotation.json and refuses to run if its sha256 differs from the .sha256 file."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROTOCOL_PATH = HERE / "protocol_cb_rotation.json"
HASH_PATH = HERE / "protocol_cb_rotation.sha256"


class ProtocolChangedError(RuntimeError):
    pass


def load_verified(protocol_path: Path = PROTOCOL_PATH, hash_path: Path = HASH_PATH) -> dict:
    raw = Path(protocol_path).read_bytes()
    actual = hashlib.sha256(raw).hexdigest()
    expected = Path(hash_path).read_text().strip().split()[0]
    if actual != expected:
        raise ProtocolChangedError(f"cb_rotation protocol hash mismatch: expected {expected}, got {actual}")
    return json.loads(raw)
