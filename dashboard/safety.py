"""Secret handling: the API key is read from the environment / .env and must never reach logs, HTML or snapshots."""
from __future__ import annotations

import logging
import os
import re
from pathlib import Path

KEY_ENV = "FUYAO_API_KEY"
_GENERIC_KEY = re.compile(r"sk-[A-Za-z0-9_\-]{8,}")


def load_env(repo_root: Path) -> None:
    """Load <repo>/.env without overriding variables that are already set (never prints values)."""
    try:
        from dotenv import load_dotenv
        load_dotenv(Path(repo_root) / ".env", override=False)
    except Exception:  # dotenv missing or unreadable file: the key simply stays unset
        pass


def api_key() -> str | None:
    k = os.environ.get(KEY_ENV)
    return k.strip() if k and k.strip() else None


def sanitize(text) -> str:
    """Replace the configured API key (and anything shaped like an sk- key) with ***."""
    s = str(text)
    k = api_key()
    if k:
        s = s.replace(k, "***")
    return _GENERIC_KEY.sub("***", s)


class RedactingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return sanitize(super().format(record))


def setup_logging(log_path: Path) -> logging.Logger:
    log_path = Path(log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("dashboard")
    logger.setLevel(logging.INFO)
    for h in list(logger.handlers):
        logger.removeHandler(h)
        h.close()
    fh = logging.FileHandler(log_path, encoding="utf-8")
    fh.setFormatter(RedactingFormatter("%(asctime)s %(levelname)s %(message)s"))
    sh = logging.StreamHandler()
    sh.setFormatter(RedactingFormatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(fh)
    logger.addHandler(sh)
    logger.propagate = False
    return logger
