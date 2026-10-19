"""Step 2: price/turnover snapshot (THS primary, akshare/Sina fallback), sha256 manifest, guarded loader.

THS /api/fund/market/historical: forward-adjusted OHLC + volume + turnover (CNY), millisecond timestamps
(Asia/Shanghai). The service only serves the most recent 5 calendar years (a start date earlier than today-5y returns
EMPTY), so ONE window [today-5y, today] is requested per ETF. History before that comes from akshare/Sina (unadjusted
bars + hfq.js distributions -> self-built total-return adjustment, as in cn_transfer) and is scaled onto THS at the
junction (median close ratio over the overlap; agreement diagnostics recorded per symbol).

Stored: ALL sessions available (including those after 2024-09-30). The loader used by scoring code clips to
guard.DEV_END at the file boundary and raises HoldoutAccessError if asked for anything later.
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd
import requests

from . import guard

HERE = Path(__file__).resolve().parent
DATA_DIR = HERE / "data"
BARS_DIR = "bars"
MANIFEST = "manifest.json"
BASE = "https://fuyao.aicubes.cn"
COLS = ["Date", "open", "high", "low", "close", "volume", "turnover"]
SH_TZ_OFFSET_MS = 8 * 3600 * 1000


class DataError(ValueError):
    pass


def sha256_file(p: Path) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def _ms(date: str, end: bool = False) -> int:
    ts = pd.Timestamp(date, tz="Asia/Shanghai")
    if end:
        ts = ts + pd.Timedelta(days=1) - pd.Timedelta(milliseconds=1)
    return int(ts.timestamp() * 1000)


class ThsClient:
    def __init__(self, key: str, throttle: float = 0.5, max_retries: int = 3):
        self.key, self.throttle, self.max_retries = key, throttle, max_retries
        self._last = 0.0
        self._lock = threading.Lock()
        self.calls = 0
        self.rate_limited = 0

    def get(self, path: str, params: dict) -> dict:
        err = None
        for attempt in range(self.max_retries + 1):
            with self._lock:                      # global >= throttle seconds between THS calls, even across threads
                wait = self.throttle - (time.time() - self._last)
                if wait > 0:
                    time.sleep(wait)
                self._last = time.time()
                self.calls += 1
            try:
                r = requests.get(BASE + path, params=params, headers={"X-api-key": self.key}, timeout=60)
                if r.status_code == 429:
                    raise _RateLimited("HTTP 429")
                j = r.json()
                if j.get("code") == 4001:
                    raise _RateLimited("code 4001")
                return j
            except _RateLimited as e:
                self.rate_limited += 1
                err = str(e)
                time.sleep(5.0 * 3 ** attempt)          # 5 s, 15 s, 45 s, 135 s back-off
            except Exception as e:  # noqa: BLE001 - network error: retry
                err = f"{type(e).__name__}"
                time.sleep(2.0 * (attempt + 1))
        raise DataError(f"THS request failed after retries: {path} {err}")


class _RateLimited(Exception):
    pass


def ths_start_date(last_date: str) -> str:
    """THS serves only the most recent 5 calendar years: a request whose start precedes (today - 5y) returns EMPTY
    (not clipped). Verified empirically on 2026-10-19: earliest servable date was 2021-10-19."""
    return (pd.Timestamp(last_date) - pd.DateOffset(years=5)).strftime("%Y-%m-%d")


def fetch_ths_bars(cl: ThsClient, thscode: str, last_date: str) -> tuple[pd.DataFrame, dict]:
    a = ths_start_date(last_date)
    end = (pd.Timestamp(last_date) - pd.Timedelta(days=1)).strftime("%Y-%m-%d")   # keep the window within 5 years
    j = cl.get("/api/fund/market/historical",
               {"thscode": thscode, "interval": "1d", "start": _ms(a), "end": _ms(end, end=True)})
    if j.get("code") != 0:
        raise DataError(f"THS code {j.get('code')} for {thscode}")
    items = j["data"]["item"] or []
    diag = {"ths_window": [a, end, len(items)]}
    if not items:
        return pd.DataFrame(columns=COLS), diag
    df = pd.DataFrame(items)
    df["Date"] = pd.to_datetime(df["date_ms"] + SH_TZ_OFFSET_MS, unit="ms").dt.strftime("%Y-%m-%d")
    df = df.rename(columns={"open_price": "open", "high_price": "high", "low_price": "low", "close_price": "close"})
    return df[COLS].drop_duplicates("Date").sort_values("Date").reset_index(drop=True), diag


def fetch_sina_full(ticker: str, exchange: str, data_dir: Path, throttle: float = 0.5) -> tuple[pd.DataFrame, dict]:
    """akshare Sina unadjusted bars (incl. amount = turnover in CNY) + Sina hfq.js distributions -> self-built
    total-return adjustment (cn_transfer.data.build_adjusted). Raw payloads are saved for audit."""
    import akshare as ak
    from research_v3.cn_transfer.data import build_adjusted, parse_hfq_js
    pre = ("sh" if exchange == "SH" else "sz") + ticker
    raw = None
    for k in range(3):
        try:
            time.sleep(throttle)
            raw = ak.fund_etf_hist_sina(symbol=pre)
            break
        except Exception:  # noqa: BLE001
            time.sleep(2.0 * (k + 1))
    if raw is None or not len(raw):
        raise DataError(f"Sina bars unavailable for {pre}")
    raw = raw.rename(columns={"date": "Date"})
    raw["Date"] = pd.to_datetime(raw["Date"]).dt.strftime("%Y-%m-%d")
    raw = raw.sort_values("Date").drop_duplicates("Date").reset_index(drop=True)
    (data_dir / "sina_raw").mkdir(exist_ok=True)
    raw[["Date", "open", "high", "low", "close", "volume", "amount"]].to_csv(data_dir / "sina_raw" / f"{ticker}.csv", index=False)
    diag = {"sina_rows": int(len(raw))}
    adj_ok = True
    txt = None
    for k in range(3):
        try:
            time.sleep(throttle)
            r = requests.get(f"https://finance.sina.com.cn/realstock/company/{pre}/hfq.js",
                             headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
            r.raise_for_status()
            txt = r.text
            break
        except Exception:  # noqa: BLE001
            time.sleep(2.0 * (k + 1))
    try:
        if txt is None:
            raise DataError("hfq.js unavailable")
        (data_dir / "sina_raw").joinpath(f"{ticker}_hfqjs.json").write_text(txt)
        cum_div, share_pts = parse_hfq_js(txt)
        bars = build_adjusted(raw[["Date", "open", "high", "low", "close", "volume"]], cum_div, share_pts)
        diag.update(dividend_points=len(cum_div), share_factor_points=len(share_pts))
    except Exception as e:  # noqa: BLE001
        adj_ok = False
        diag["adjustment_failed"] = f"{type(e).__name__}: {str(e)[:100]}"
        bars = raw[["Date", "open", "high", "low", "close", "volume"]].copy()
        bars["adj_open"], bars["adj_close"] = bars["open"], bars["close"]
    f = bars["adj_close"] / bars["close"]
    out = pd.DataFrame({"Date": bars["Date"], "open": bars["adj_open"], "high": bars["high"] * f, "low": bars["low"] * f,
                        "close": bars["adj_close"], "volume": bars["volume"], "turnover": raw["amount"].to_numpy(float)})
    diag["sina_adjusted"] = adj_ok
    return out[COLS], diag


def merge_sources(ths: pd.DataFrame, sina: pd.DataFrame | None) -> tuple[pd.DataFrame, dict]:
    """THS (forward-adjusted) is primary; Sina total-return-adjusted bars extend it BEFORE THS's first date, scaled
    onto THS by the median close ratio over the overlap. Overlap agreement is recorded."""
    d: dict = {}
    if sina is None or not len(sina):
        d["source"] = "THS_only"
        return ths, d
    if ths is None or not len(ths):
        d["source"] = "Sina_selfbuilt_only"
        return sina, d
    ov = ths.merge(sina, on="Date", suffixes=("_t", "_s"))
    d["overlap_rows"] = int(len(ov))
    d["ths_dates_missing_in_sina"] = int((~ths.Date.isin(sina.Date)).sum())
    if len(ov) < 5:
        d["source"] = "THS_only(no overlap with Sina)"
        return ths, d
    ratio = ov["close_t"] / ov["close_s"]
    scale = float(np.median(ratio))
    d["junction_scale"] = scale
    d["overlap_ratio_max_dev"] = float(np.max(np.abs(ratio / scale - 1.0)))
    rt, rs = np.diff(np.log(ov["close_t"].to_numpy())), np.diff(np.log(ov["close_s"].to_numpy()))
    d["overlap_daily_logret_max_abs_diff"] = float(np.max(np.abs(rt - rs))) if len(rt) else 0.0
    d["overlap_turnover_median_ratio"] = float(np.nanmedian((ov["turnover_t"] / ov["turnover_s"].replace(0, np.nan)).to_numpy()))
    first = ths["Date"].iloc[0]
    pre = sina[sina["Date"] < first].copy()
    for c in ("open", "high", "low", "close"):
        pre[c] = pre[c] * scale
    out = pd.concat([pre, ths]).sort_values("Date").reset_index(drop=True)
    d["source"] = "THS(>=%s)+Sina_ext(before)" % first
    d["sina_ext_rows"] = int(len(pre))
    return out, d


def sector_snapshot(tickers: list[dict], key: str, data_dir: Path = DATA_DIR, last_date: str | None = None,
                    log=print, workers: int = 6) -> dict:
    """tickers: list of {ticker, exchange}. Resumable: symbols already marked ok are skipped."""
    data_dir = Path(data_dir)
    (data_dir / BARS_DIR).mkdir(parents=True, exist_ok=True)
    last_date = last_date or pd.Timestamp.now(tz="Asia/Shanghai").strftime("%Y-%m-%d")
    cl = ThsClient(key)
    info_p = data_dir / "fetch_info.json"
    info = json.loads(info_p.read_text()) if info_p.exists() else {}
    lock = threading.Lock()

    def work(k_t):
        k, t = k_t
        sym = t["ticker"]
        if sym in info and info[sym].get("status") == "ok" and (data_dir / BARS_DIR / f"{sym}.csv").exists():
            return
        thscode = f"{sym}.{t['exchange']}"
        rec = {"thscode": thscode}
        ths = sina = None
        try:
            ths, dg = fetch_ths_bars(cl, thscode, last_date)
            rec.update(dg)
        except Exception as e:  # noqa: BLE001
            rec["ths_error"] = f"{type(e).__name__}: {str(e)[:150]}"
        try:
            sina, dg = fetch_sina_full(sym, t["exchange"], data_dir)
            rec.update(dg)
        except Exception as e:  # noqa: BLE001
            rec["sina_error"] = f"{type(e).__name__}: {str(e)[:150]}"
        if (ths is None or not len(ths)) and (sina is None or not len(sina)):
            rec.update(source="NONE", status="failed")
            df = None
        else:
            df, dg = merge_sources(ths, sina)
            rec.update(dg)
            rec.update(status="ok", rows=int(len(df)), first=df["Date"].iloc[0], last=df["Date"].iloc[-1])
            df.to_csv(data_dir / BARS_DIR / f"{sym}.csv", index=False)
        with lock:
            info[sym] = rec
            info_p.write_text(json.dumps(info, indent=1))
            if rec["status"] != "ok":
                log(f"[{k+1}/{len(tickers)}] {sym} FAILED")
            elif (k + 1) % 20 == 0:
                log(f"[{k+1}/{len(tickers)}] ths_calls={cl.calls} rate_limited={cl.rate_limited}")

    with ThreadPoolExecutor(max_workers=workers) as ex:
        list(ex.map(work, list(enumerate(tickers))))
    return info


def write_manifest(data_dir: Path = DATA_DIR, extra: dict | None = None) -> dict:
    data_dir = Path(data_dir)
    files = {}
    for p in sorted(data_dir.rglob("*")):
        if p.is_file() and p.name not in (MANIFEST, ".DS_Store"):
            files[str(p.relative_to(data_dir))] = {"sha256": sha256_file(p)}
    man = {"created_utc": pd.Timestamp.now(tz="UTC").isoformat(), "files": files, **(extra or {})}
    (data_dir / MANIFEST).write_text(json.dumps(man, indent=1, sort_keys=True) + "\n")
    return man


def verify_manifest(data_dir: Path = DATA_DIR) -> dict:
    data_dir = Path(data_dir)
    man = json.loads((data_dir / MANIFEST).read_text())
    for name, meta in man["files"].items():
        p = data_dir / name
        if not p.is_file():
            raise DataError(f"snapshot file missing: {name}")
        if sha256_file(p) != meta["sha256"]:
            raise DataError(f"snapshot hash mismatch for {name}")
    return man


def load_bars_for_scoring(tickers, data_dir: Path = DATA_DIR, through: str = guard.DEV_END,
                          verify: bool = True) -> dict[str, pd.DataFrame]:
    """Hash-verified load; rows after DEV_END are dropped here and an explicit request beyond DEV_END raises."""
    guard.assert_date_allowed(through)
    if verify:
        verify_manifest(data_dir)
    out = {}
    for t in tickers:
        df = pd.read_csv(Path(data_dir) / BARS_DIR / f"{t}.csv", dtype={"Date": str})
        out[t] = guard.clip_to_dev(df[df["Date"] <= through])
    return out


def load_shibor_for_scoring(data_dir: Path = DATA_DIR, through: str = guard.DEV_END, verify: bool = True) -> pd.Series:
    guard.assert_date_allowed(through)
    if verify:
        verify_manifest(data_dir)
    df = pd.read_csv(Path(data_dir) / "shibor_3m.csv", dtype={"Date": str})
    df = df[df["Date"] <= through]
    if not df["Date"].is_unique:
        raise DataError("duplicate SHIBOR dates")
    return pd.Series(df["shibor_3m_pct"].values, index=df["Date"].values, dtype=float)
