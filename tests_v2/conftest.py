"""Shared fixtures and network isolation for tests_v2."""
from __future__ import annotations

import socket
from pathlib import Path
import pytest


@pytest.fixture(autouse=True)
def disable_network(monkeypatch):
    """Ensure tests_v2 never accesses the network."""
    def guarded_connect(*args, **kwargs):
        raise RuntimeError("Network access forbidden in tests_v2")
    monkeypatch.setattr(socket.socket, "connect", guarded_connect)


@pytest.fixture
def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]
