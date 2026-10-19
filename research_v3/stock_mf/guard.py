"""Holdout guard for stock_mf. Protocol: holdout 2024-04-01 .. 2026-09-30 must not be loaded, scored, plotted or
summarised before a one-time run authorised by the user in chat after the development verdict.

Raw files may contain sessions after DEV_END (downloaded and stored), but every scoring-side loader filters them out at
the file boundary and every scoring entry point calls the asserts below. The limit is lifted only inside open_holdout(),
which needs an authorisation file ({"authorized": true}) and a run-state file with status "started". Neither file
exists in the development phase.
"""
from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path
from typing import Iterable

DEV_END = "2024-03-31"
HOLDOUT_START = "2024-04-01"
HOLDOUT_END = "2026-09-30"

_HERE = Path(__file__).resolve().parent
_AUTH_PATH = _HERE / "HOLDOUT_AUTHORIZATION.json"
_STATE_PATH = _HERE / "holdout_state.json"

_limit = DEV_END


class HoldoutAccessError(ValueError):
    pass


def current_limit() -> str:
    return _limit


@contextmanager
def open_holdout(auth_path: Path = _AUTH_PATH, state_path: Path = _STATE_PATH):
    global _limit
    try:
        auth = json.loads(Path(auth_path).read_text())
        state = json.loads(Path(state_path).read_text())
    except (OSError, ValueError) as e:
        raise HoldoutAccessError(f"holdout cannot be opened: authorization/state file missing or invalid ({e})")
    if not (isinstance(auth, dict) and auth.get("authorized") is True):
        raise HoldoutAccessError("holdout cannot be opened: authorization file does not contain authorized=true")
    if not (isinstance(state, dict) and state.get("status") == "started"):
        raise HoldoutAccessError("holdout cannot be opened: run state is not 'started'")
    prev, _limit = _limit, HOLDOUT_END
    try:
        yield
    finally:
        _limit = prev


def assert_date_allowed(date: str) -> None:
    if str(date)[:10] > _limit:
        raise HoldoutAccessError(f"session {date} is after {_limit}: holdout data must not be loaded for scoring")


def assert_dates_allowed(dates: Iterable[str]) -> None:
    mx = max((str(d)[:10] for d in dates), default=None)
    if mx is not None:
        assert_date_allowed(mx)


def clip_to_dev(df, date_col: str = "date"):
    """Drop rows beyond the current limit (DEV_END unless inside open_holdout)."""
    return df[df[date_col].astype(str).str[:10] <= _limit].reset_index(drop=True)
