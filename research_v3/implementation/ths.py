"""Small THS (fuyao) client for the implementation checks. The key is read from the environment and never printed."""
from __future__ import annotations

import time

import pandas as pd
import requests

from dashboard.safety import api_key, load_env, sanitize
from dashboard.config import REPO, SHANGHAI

BASE = "https://fuyao.aicubes.cn"


def _get(path: str, params: dict, tries: int = 3):
    load_env(REPO)
    key = api_key()
    if not key:
        raise RuntimeError("FUYAO_API_KEY not set")
    last = None
    for i in range(tries):
        try:
            time.sleep(0.6)
            r = requests.get(BASE + path, headers={"X-api-key": key}, params=params, timeout=30)
            r.raise_for_status()
            return r.json()
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(1.5)
    raise RuntimeError(sanitize(f"{path}: {type(last).__name__}: {last}"))


def fund_bars(code: str, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    ms = lambda t: int(pd.Timestamp(t, tz=SHANGHAI).timestamp() * 1000)
    j = _get("/api/fund/market/historical", {"thscode": code, "interval": "1d", "start": ms(start), "end": ms(end)})
    items = (j.get("data") or {}).get("item")
    if j.get("code") != 0 or not items:
        raise RuntimeError(f"THS {code}: code={j.get('code')} msg={str(j.get('message'))[:100]}")
    df = pd.DataFrame(items)
    df["Date"] = pd.to_datetime(df["date_ms"], unit="ms", utc=True).dt.tz_convert(SHANGHAI).dt.strftime("%Y-%m-%d")
    return df
