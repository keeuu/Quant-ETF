"""Alpaca market-data fallback (READ-ONLY). Only the historical-bars data endpoint is ever requested.

No trading / account / orders endpoint is used anywhere; `_get` refuses every URL that is not on the market-data host
under /v2/stocks/... or /v1/corporate-actions. Keys go only into request headers and are redacted by safety.sanitize.
"""
from __future__ import annotations

import logging
from urllib.parse import urlparse

import pandas as pd

from .safety import alpaca_keys, sanitize

log = logging.getLogger("dashboard")

DATA_HOST = "data.alpaca.markets"
BARS_URL = f"https://{DATA_HOST}/v2/stocks/{{sym}}/bars"
ALLOWED_PATH_PREFIXES = ("/v2/stocks/", "/v1/corporate-actions")
BAR_COLUMNS = ["Date", "open", "high", "low", "close", "adj_close", "adj_open", "volume"]


class AlpacaError(RuntimeError):
    pass


def _check_url(url: str) -> None:
    u = urlparse(url)
    if u.scheme != "https" or u.netloc != DATA_HOST or not u.path.startswith(ALLOWED_PATH_PREFIXES) \
            or "order" in u.path or "account" in u.path:
        raise AlpacaError(f"refused non-market-data URL: {u.netloc}{u.path}")


def _get(url: str, params: dict, http=None, timeout: float = 30):
    """GET one market-data page; returns parsed JSON. Errors are sanitized (keys redacted)."""
    _check_url(url)
    key, secret = alpaca_keys()
    if not key or not secret:
        raise AlpacaError("ALPACA_API_KEY / ALPACA_SECRET_KEY not configured")
    if http is None:
        import requests as http
    try:
        r = http.get(url, params=params, timeout=timeout,
                     headers={"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret, "accept": "application/json"})
        r.raise_for_status()
        return r.json()
    except AlpacaError:
        raise
    except Exception as e:  # noqa: BLE001
        raise AlpacaError(sanitize(f"Alpaca request failed: {type(e).__name__}: {e}")) from None


def _pages(sym: str, adjustment: str, start: str, end: str | None, http=None) -> list:
    rows, token = [], None
    for _ in range(200):                                           # hard stop against a looping token
        params = {"timeframe": "1Day", "start": start, "adjustment": adjustment, "feed": "iex", "limit": 10000,
                  "sort": "asc"}
        if end:
            params["end"] = end
        if token:
            params["page_token"] = token
        j = _get(BARS_URL.format(sym=sym), params, http=http)
        b = j.get("bars") or []
        if isinstance(b, dict):                                    # multi-symbol shape
            b = b.get(sym) or []
        rows.extend(b)
        token = j.get("next_page_token")
        if not token:
            return rows
    raise AlpacaError(f"{sym}: pagination did not terminate")


def _frame(rows: list) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    if df.empty:
        return pd.DataFrame(columns=["Date", "o", "h", "l", "c", "v"])
    df["Date"] = df["t"].astype(str).str[:10]                      # daily bars are stamped at ET midnight
    return df[["Date", "o", "h", "l", "c", "v"]].drop_duplicates("Date", keep="last").sort_values("Date")


def fetch_alpaca_bars(sym: str, start: str = "2018-01-01", end: str | None = None, http=None) -> pd.DataFrame:
    """Daily bars in the research bar format. Raw OHLC from adjustment=raw, adj_close from adjustment=all
    (splits + dividends), adj_open = open*adj_close/close like paper_v2.normalize_yf_frame."""
    raw = _frame(_pages(sym, "raw", start, end, http)).set_index("Date")
    adj = _frame(_pages(sym, "all", start, end, http)).set_index("Date")
    if raw.empty or adj.empty:
        raise AlpacaError(f"{sym}: Alpaca returned no bars")
    idx = raw.index.intersection(adj.index)
    raw, adj = raw.loc[idx], adj.loc[idx]
    out = pd.DataFrame({"Date": idx, "open": raw["o"].astype(float).values, "high": raw["h"].astype(float).values,
                        "low": raw["l"].astype(float).values, "close": raw["c"].astype(float).values,
                        "adj_close": adj["c"].astype(float).values, "volume": raw["v"].astype(float).values})
    out["adj_open"] = out["open"] * out["adj_close"] / out["close"]
    return out[BAR_COLUMNS].reset_index(drop=True)


def latest_close(sym: str, http=None, lookback_start: str | None = None) -> float:
    start = lookback_start or (pd.Timestamp.now() - pd.Timedelta(days=15)).strftime("%Y-%m-%d")
    rows = _pages(sym, "raw", start, None, http)
    if not rows:
        raise AlpacaError(f"{sym}: Alpaca returned no bars")
    return float(rows[-1]["c"])
