"""Hash-verified loader for protocol_sector_mom_amendment1.json (never edited)."""
from pathlib import Path
from .protocol import load_verified, HERE

AMEND_PATH = HERE / "protocol_sector_mom_amendment1.json"
AMEND_HASH = HERE / "protocol_sector_mom_amendment1.sha256"


def load_amendment_verified(path: Path = AMEND_PATH, hash_path: Path = AMEND_HASH) -> dict:
    return load_verified(path, hash_path)
