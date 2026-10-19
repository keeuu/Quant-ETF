import socket
import pytest


@pytest.fixture(autouse=True)
def disable_network(monkeypatch):
    def guarded(*a, **k):
        raise RuntimeError("Network access forbidden in tests_v3")
    monkeypatch.setattr(socket.socket, "connect", guarded)
