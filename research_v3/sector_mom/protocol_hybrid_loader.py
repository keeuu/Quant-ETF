"""Hash-verified loader for protocol_hybrid.json (never edited)."""
from pathlib import Path
from .protocol import HERE, load_verified

HYBRID_PATH = HERE / "protocol_hybrid.json"
HYBRID_HASH = HERE / "protocol_hybrid.sha256"


def load_hybrid_verified(path: Path = HYBRID_PATH, hash_path: Path = HYBRID_HASH) -> dict:
    return load_verified(path, hash_path)
