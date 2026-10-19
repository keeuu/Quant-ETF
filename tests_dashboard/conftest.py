"""tests_dashboard: no network, dummy API key (the real key is never needed or read)."""
import socket
import warnings

import pytest

DUMMY_KEY = "sk-dummyDASHBOARDkey1234567890abcdef"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("FUYAO_API_KEY", DUMMY_KEY)

    def guarded(*a, **k):
        raise RuntimeError("Network access forbidden in tests_dashboard")
    monkeypatch.setattr(socket.socket, "connect", guarded)


warnings.filterwarnings("ignore")
