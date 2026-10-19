"""Minimal Alpaca PAPER-only REST client.

Safety (independent of V1's ORDER_SUBMISSION_ENABLED kill switch, which this module never touches):
  * the base URL is a constant; `_guard` re-checks scheme + exact host against ALLOWED_HOSTS before EVERY request
    (the live host api.alpaca.markets is refused, as is any look-alike, userinfo or port trick);
  * GET only on a fixed read-only path list; the only POST allowed is /v2/orders, and only when the client was
    built with allow_submit=True; no DELETE/PATCH/PUT exists at all (nothing is ever cancelled or modified);
  * redirects are never followed; keys only go into request headers and every message is passed through sanitize.
"""
from __future__ import annotations

import logging
import os
from urllib.parse import urlparse

from dashboard.safety import alpaca_keys, sanitize

log = logging.getLogger("paper_exec")

PAPER_HOST = "paper-api.alpaca.markets"
ALLOWED_HOSTS = frozenset({PAPER_HOST})
BASE_URL = f"https://{PAPER_HOST}"
READ_PATHS = frozenset({"/v2/account", "/v2/clock", "/v2/calendar", "/v2/positions", "/v2/orders",
                        "/v2/orders:by_client_order_id"})
READ_PREFIXES = ("/v2/assets/",)
SUBMIT_PATH = "/v2/orders"


class SecurityError(RuntimeError):
    pass


class BrokerError(RuntimeError):
    def __init__(self, status: int, message: str):
        super().__init__(sanitize(f"HTTP {status}: {message}"))
        self.status = status


class SubmissionUnknown(RuntimeError):
    """POST outcome unknown (timeout / connection error). Never retried; resolve via client_order_id lookup."""


def check_request(method: str, url: str, allow_submit: bool = False) -> None:
    """Raise SecurityError unless (method, url) is an allowed paper-host request."""
    u = urlparse(url)
    host_ok = (u.scheme == "https" and u.hostname is not None and u.hostname.lower() in ALLOWED_HOSTS
               and u.netloc.lower() in ALLOWED_HOSTS and not u.username and not u.password)
    if not host_ok:
        raise SecurityError(f"拒绝：主机 '{u.netloc}' 不在模拟账户白名单 {sorted(ALLOWED_HOSTS)}")
    m = method.upper()
    if m == "GET":
        if u.path in READ_PATHS or u.path.startswith(READ_PREFIXES):
            return
        raise SecurityError(f"拒绝：只读白名单外的路径 {u.path}")
    if m == "POST" and u.path == SUBMIT_PATH:
        if not allow_submit:
            raise SecurityError("拒绝：该客户端未获下单授权(allow_submit=False)")
        return
    raise SecurityError(f"拒绝：不允许的请求 {m} {u.path}")


def _default_http(method, url, headers, params, json_body, timeout):
    import requests
    r = requests.request(method, url, headers=headers, params=params, json=json_body, timeout=timeout,
                         allow_redirects=False)
    try:
        body = r.json()
    except Exception:  # noqa: BLE001
        body = r.text
    return r.status_code, body


class PaperBroker:
    def __init__(self, *, allow_submit: bool = False, http=None, keys: tuple | None = None, timeout: float = 20.0):
        if os.environ.get("ALPACA_BASE_URL") and urlparse(os.environ["ALPACA_BASE_URL"]).netloc.lower() not in ALLOWED_HOSTS:
            log.warning("ALPACA_BASE_URL 指向非模拟主机，paper_exec 忽略它，固定使用 %s", PAPER_HOST)
        self.allow_submit = bool(allow_submit)
        self._http = http or _default_http
        self._keys = keys
        self.timeout = timeout

    def __repr__(self):
        return f"<PaperBroker host={PAPER_HOST} allow_submit={self.allow_submit}>"

    def _headers(self) -> dict:
        key, secret = self._keys if self._keys else alpaca_keys()
        if not key or not secret:
            raise SecurityError("ALPACA_API_KEY / ALPACA_SECRET_KEY 未配置(.env)")
        return {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret, "accept": "application/json"}

    def _request(self, method: str, path: str, params: dict | None = None, body: dict | None = None):
        url = BASE_URL + path
        check_request(method, url, self.allow_submit)          # <- whitelist guard before EVERY request
        return self._http(method, url, self._headers(), params, body, self.timeout)

    def _get(self, path: str, params: dict | None = None, retries: int = 2, ok404: bool = False):
        last = None
        for _ in range(retries + 1):
            try:
                status, body = self._request("GET", path, params)
            except SecurityError:
                raise
            except Exception as e:  # noqa: BLE001  network blip: reads are idempotent
                last = BrokerError(0, f"{type(e).__name__}: {e}")
                continue
            if status == 404 and ok404:
                return None
            if status >= 500:
                last = BrokerError(status, str(body)[:200])
                continue
            if status >= 400:
                raise BrokerError(status, str(body)[:200])
            return body
        raise last

    # ---- read-only endpoints
    def get_account(self) -> dict:
        return self._get("/v2/account")

    def get_clock(self) -> dict:
        return self._get("/v2/clock")

    def get_calendar(self, start: str, end: str) -> list:
        return self._get("/v2/calendar", {"start": start, "end": end})

    def get_positions(self) -> list:
        return self._get("/v2/positions")

    def list_orders(self, status: str = "all", limit: int = 100, after: str | None = None) -> list:
        p = {"status": status, "limit": limit, "direction": "desc"}
        if after:
            p["after"] = after
        return self._get("/v2/orders", p)

    def get_order_by_client_id(self, client_order_id: str):
        return self._get("/v2/orders:by_client_order_id", {"client_order_id": client_order_id}, ok404=True)

    def get_asset(self, symbol: str) -> dict:
        return self._get(f"/v2/assets/{symbol}")

    # ---- the single write path
    def submit_market_order(self, *, symbol: str, side: str, client_order_id: str, notional: float | None = None,
                            qty: float | str | None = None) -> dict:
        if not self.allow_submit:
            raise SecurityError("拒绝：该客户端未获下单授权(allow_submit=False)")
        if side not in ("buy", "sell"):
            raise ValueError(f"bad side {side}")
        if (notional is None) == (qty is None):
            raise ValueError("必须且只能指定 notional 或 qty")
        if not client_order_id:
            raise ValueError("缺少 client_order_id")
        body = {"symbol": symbol, "side": side, "type": "market", "time_in_force": "day",
                "client_order_id": client_order_id, "extended_hours": False}
        if notional is not None:
            body["notional"] = f"{float(notional):.2f}"
        else:
            body["qty"] = str(qty)
        try:
            status, resp = self._request("POST", SUBMIT_PATH, None, body)    # single attempt, never retried
        except SecurityError:
            raise
        except Exception as e:  # noqa: BLE001
            raise SubmissionUnknown(sanitize(f"{client_order_id}: {type(e).__name__}: {e}")) from None
        if status >= 500:
            raise SubmissionUnknown(f"{client_order_id}: HTTP {status}")
        if status >= 400:
            raise BrokerError(status, str(resp)[:300])
        return resp
