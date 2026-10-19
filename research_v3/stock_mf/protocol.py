"""Loads protocol_stock_mf.json and refuses to run if its sha256 differs from the committed hash."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROTOCOL_PATH = HERE / "protocol_stock_mf.json"
HASH_PATH = HERE / "protocol_stock_mf.sha256"


class ProtocolChangedError(RuntimeError):
    pass


def load_verified(protocol_path: Path = PROTOCOL_PATH, hash_path: Path = HASH_PATH) -> dict:
    raw = Path(protocol_path).read_bytes()
    actual = hashlib.sha256(raw).hexdigest()
    expected = Path(hash_path).read_text().strip()
    if actual != expected:
        raise ProtocolChangedError(f"stock_mf protocol hash mismatch: expected {expected}, got {actual}")
    return json.loads(raw)


AMEND1_PATH = HERE / "protocol_stock_mf_amendment1.json"
AMEND1_HASH = HERE / "protocol_stock_mf_amendment1.sha256"


def load_verified_amendment1() -> dict:
    """Verifies the base protocol AND the (uncommitted) amendment 1 by their .sha256 files."""
    load_verified()
    return load_verified(AMEND1_PATH, AMEND1_HASH)
