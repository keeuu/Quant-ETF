"""Pre-registered step-4 protocol loader: refuses to run if the protocol file hash changed."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROTOCOL_PATH = HERE / "protocol_step4.json"
HASH_PATH = HERE / "protocol_step4.sha256"


class ProtocolChangedError(RuntimeError):
    pass


def load_verified(protocol_path: Path = PROTOCOL_PATH, hash_path: Path = HASH_PATH) -> dict:
    raw = Path(protocol_path).read_bytes()
    actual = hashlib.sha256(raw).hexdigest()
    expected = Path(hash_path).read_text().strip()
    if actual != expected:
        raise ProtocolChangedError(f"step4 protocol hash mismatch: expected {expected}, got {actual}")
    return json.loads(raw)
