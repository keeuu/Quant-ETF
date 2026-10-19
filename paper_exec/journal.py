"""Append-only execution journal (JSONL). One signal date is executed at most once: see `state`."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from dashboard.safety import sanitize

PATH = Path(__file__).resolve().parent / "journal.jsonl"


def append(event: dict, path: Path | None = None) -> None:
    path = Path(path or PATH)
    rec = {"ts": datetime.now(timezone.utc).isoformat(), **event}
    line = sanitize(json.dumps(rec, ensure_ascii=False, sort_keys=True, default=str))
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def read(path: Path | None = None) -> list[dict]:
    path = Path(path or PATH)
    if not path.exists():
        return []
    out = []
    for ln in path.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(ln))
        except Exception:  # noqa: BLE001
            continue
    return out


def state(signal_date: str, path: Path | None = None) -> dict:
    """{'complete': bool, 'plan': list|None, 'events': [...]} for one signal date."""
    ev = [e for e in read(path) if e.get("signal_date") == signal_date]
    plan = None
    for e in ev:
        if e.get("event") == "plan":
            plan = e["orders"]
    return {"complete": any(e.get("event") == "complete" for e in ev), "plan": plan, "events": ev}
