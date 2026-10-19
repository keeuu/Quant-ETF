"""tests_paper_exec: network fully blocked, dummy Alpaca keys, scripted fake paper API (no real HTTP, ever)."""
import socket
import warnings

import pytest

KEY = "PKTESTKEYABCDEF123456"
SECRET = "SECRETTESTVALUE9876543210xyz"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("ALPACA_API_KEY", KEY)
    monkeypatch.setenv("ALPACA_SECRET_KEY", SECRET)
    monkeypatch.delenv("ALPACA_BASE_URL", raising=False)

    def guarded(*a, **k):
        raise RuntimeError("Network access forbidden in tests_paper_exec")
    monkeypatch.setattr(socket.socket, "connect", guarded)


class FakeAlpaca:
    """Scripted stand-in for the paper REST API. Records every call; market orders fill instantly."""

    def __init__(self, equity=100000.0, cash=100000.0, positions=None, now="2026-11-02T09:40:00-05:00", is_open=True,
                 days=None):
        self.equity, self.cash = equity, cash
        self.positions = positions or {}            # sym -> market_value
        self.now, self.is_open = now, is_open
        self.days = days or ["2026-10-27", "2026-10-28", "2026-10-29", "2026-10-30", "2026-11-02"]
        self.orders = {}                            # client_order_id -> order dict
        self.calls = []
        self.reject = set()                         # client_order_ids that get HTTP 422 once
        self.nonfrac = set()

    def posts(self):
        return [c for c in self.calls if c[0] == "POST"]

    def __call__(self, method, url, headers, params, body, timeout):
        self.calls.append((method, url, dict(body or {}), dict(params or {})))
        path = url.split("alpaca.markets", 1)[1]
        if method == "POST":
            cid = body["client_order_id"]
            if cid in self.reject:
                self.reject.discard(cid)
                return 422, {"message": f"insufficient buying power key={KEY}"}
            if cid in self.orders:
                return 422, {"message": "client_order_id must be unique"}
            amt = float(body["notional"]) if "notional" in body else float(body["qty"]) * 100
            sgn = 1 if body["side"] == "buy" else -1
            self.cash -= sgn * amt
            self.positions[body["symbol"]] = self.positions.get(body["symbol"], 0.0) + sgn * amt
            if self.positions[body["symbol"]] < 1e-6:
                self.positions.pop(body["symbol"])
            o = {"id": "oid-" + cid, "client_order_id": cid, "symbol": body["symbol"], "side": body["side"],
                 "status": "filled", "qty": body.get("qty"), "notional": body.get("notional"),
                 "filled_qty": body.get("qty") or "1", "filled_avg_price": "100"}
            self.orders[cid] = o
            return 200, o
        if path == "/v2/account":
            return 200, {"status": "ACTIVE", "equity": str(self.equity), "cash": str(self.cash),
                         "buying_power": str(self.cash), "id": "ACCT-NUMBER-SECRET"}
        if path == "/v2/clock":
            return 200, {"timestamp": self.now, "is_open": self.is_open, "next_open": "x", "next_close": "y"}
        if path == "/v2/calendar":
            return 200, [{"date": d, "open": "09:30", "close": "16:00"} for d in self.days]
        if path == "/v2/positions":
            return 200, [{"symbol": s, "qty": str(v / 100), "market_value": str(v), "current_price": "100",
                          "side": "long"} for s, v in self.positions.items()]
        if path.startswith("/v2/assets/"):
            s = path.rsplit("/", 1)[1]
            return 200, {"symbol": s, "tradable": True, "fractionable": s not in self.nonfrac, "status": "active"}
        if path == "/v2/orders:by_client_order_id":
            o = self.orders.get(params["client_order_id"])
            return (200, o) if o else (404, {"message": "order not found"})
        if path == "/v2/orders":
            return 200, list(self.orders.values())
        return 404, {}


warnings.filterwarnings("ignore")
