"""Hash-verified loaders for the two pre-registered us_long protocols. Refuse to run on any mismatch."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
MAIN = ("protocol_us_long.json", "protocol_us_long.sha256")
CRASH = ("protocol_challenger_crash.json", "protocol_challenger_crash.sha256")


class ProtocolChangedError(RuntimeError):
    pass


def _load(pair, base: Path = HERE) -> dict:
    raw = (base / pair[0]).read_bytes()
    actual = hashlib.sha256(raw).hexdigest()
    expected = (base / pair[1]).read_text().strip()
    if actual != expected:
        raise ProtocolChangedError(f"{pair[0]} hash mismatch: expected {expected}, got {actual}")
    return json.loads(raw)


def load_main(base: Path = HERE) -> dict:
    return _load(MAIN, base)


def load_crash(base: Path = HERE) -> dict:
    return _load(CRASH, base)
