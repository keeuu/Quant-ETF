import hashlib
import json
import os
import subprocess

from .config import config_dict


def strategy_commit() -> str:
    """Current git HEAD of the repo containing this package; 'UNKNOWN' if unavailable."""
    repo = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, timeout=10
        )
        sha = out.stdout.strip()
        if out.returncode == 0 and len(sha) == 40:
            return sha
    except (OSError, subprocess.SubprocessError):
        pass
    return "UNKNOWN"


def config_hash() -> str:
    """sha256 of canonical JSON (sorted keys, no whitespace) of config_dict()."""
    canon = json.dumps(config_dict(), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()
