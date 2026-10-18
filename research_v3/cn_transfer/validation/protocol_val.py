"""Loads protocol_cn_validation.json and refuses to run if its sha256 differs from the committed hash."""
from __future__ import annotations

from pathlib import Path

from ..protocol import ProtocolChangedError, load_verified  # same hash gate, reused by import

HERE = Path(__file__).resolve().parent
CN_DIR = HERE.parent
VAL_PROTOCOL_PATH = CN_DIR / "protocol_cn_validation.json"
VAL_HASH_PATH = CN_DIR / "protocol_cn_validation.sha256"

__all__ = ["ProtocolChangedError", "load_validation_protocol", "VAL_PROTOCOL_PATH", "VAL_HASH_PATH"]


def load_validation_protocol(path: Path = VAL_PROTOCOL_PATH, hash_path: Path = VAL_HASH_PATH) -> dict:
    """Raises ProtocolChangedError if sha256(file) != committed hash."""
    return load_verified(Path(path), Path(hash_path))
