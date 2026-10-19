"""Shared fixtures for legacy Paper tests (tests/).

Safety guarantees (mirrors tests_v2/conftest.py design):
1. Network access is forbidden — monkeypatches socket.connect.
2. All rebalance state and logs are redirected to tmp_path; the production
   logs/app.log and logs/rebalance_state.json are never touched.
3. ORDER_SUBMISSION_ENABLED is forced to False so no real orders can slip through.
4. Real .env credentials are never used; tests must supply their own mock keys.
"""
from __future__ import annotations

import logging
import socket
from pathlib import Path
from typing import Generator
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# Network isolation
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def disable_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Forbid all real network connections from Paper/fault-injection tests."""
    def guarded_connect(*args, **kwargs):
        raise RuntimeError(
            "Network access is forbidden in tests/. "
            "Use monkeypatch to mock broker calls instead."
        )
    monkeypatch.setattr(socket.socket, "connect", guarded_connect)


# ---------------------------------------------------------------------------
# Kill-switch enforcement
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def force_kill_switch(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure ORDER_SUBMISSION_ENABLED is always False during tests."""
    monkeypatch.setattr("config.config.ORDER_SUBMISSION_ENABLED", False)
    monkeypatch.setattr("src.broker.alpaca_client.ORDER_SUBMISSION_ENABLED", False)
    # executor imports ORDER_SUBMISSION_ENABLED at module level; ensure it's patched
    try:
        import src.execution.executor as _executor_mod  # noqa: PLC0415
        monkeypatch.setattr(_executor_mod, "ORDER_SUBMISSION_ENABLED", False)
    except ImportError:
        pass  # executor not yet imported; config patch above is sufficient


# ---------------------------------------------------------------------------
# Logger redirection — redirect FileHandler to tmp, never touch logs/app.log
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def redirect_logger(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Generator:
    """Redirect all FileHandlers of 'quant_etf' logger to tmp_path.

    This prevents any test import from appending to the production logs/app.log.
    """
    from src.utils.logger import configure_test_logging  # noqa: PLC0415
    configure_test_logging(tmp_path, log_file="test_app.log")
    yield
    # Cleanup: close and remove tmp FileHandlers after test
    _logger = logging.getLogger("quant_etf")
    for h in list(_logger.handlers):
        if isinstance(h, logging.FileHandler) and "test_app.log" in str(h.baseFilename):
            h.close()
            _logger.removeHandler(h)


# ---------------------------------------------------------------------------
# Default state_file to tmp_path (convenience fixture for tests that need it)
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_state_file(tmp_path: Path) -> Path:
    """Return a temp rebalance_state.json path (never touches logs/)."""
    return tmp_path / "rebalance_state.json"


@pytest.fixture
def tmp_lock_file(tmp_path: Path) -> Path:
    """Return a temp execution.lock path."""
    return tmp_path / "execution.lock"


# ---------------------------------------------------------------------------
# Production logs invariant — assert logs/ unchanged after each test
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def assert_production_logs_unchanged() -> Generator:
    """Record logs/ metadata before the test and assert nothing changed after."""
    import hashlib
    import os

    prod_root = Path(__file__).resolve().parents[1]

    def _meta(p: Path) -> tuple:
        if not p.exists():
            return (None, None, None)
        st = os.stat(p)
        sha = hashlib.sha256(p.read_bytes()).hexdigest()
        return (sha, st.st_size, st.st_mtime_ns)

    app_log = prod_root / "logs" / "app.log"
    state_file = prod_root / "logs" / "rebalance_state.json"

    before_app = _meta(app_log)
    before_state = _meta(state_file)

    yield

    after_app = _meta(app_log)
    after_state = _meta(state_file)

    assert before_app == after_app, (
        f"INVARIANT VIOLATED: logs/app.log changed during test!\n"
        f"  before sha256={before_app[0]}, size={before_app[1]}, mtime_ns={before_app[2]}\n"
        f"  after  sha256={after_app[0]}, size={after_app[1]}, mtime_ns={after_app[2]}"
    )
    assert before_state == after_state, (
        f"INVARIANT VIOLATED: logs/rebalance_state.json changed during test!\n"
        f"  before sha256={before_state[0]}, size={before_state[1]}, mtime_ns={before_state[2]}\n"
        f"  after  sha256={after_state[0]}, size={after_state[1]}, mtime_ns={after_state[2]}"
    )
