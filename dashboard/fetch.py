"""Data acquisition and snapshotting. US: yfinance. CN: THS (primary), Sina + cn_transfer adjustment, Eastmoney.

Every function returns plain pandas data in the research bar format; nothing here touches strategy logic.
The API key is only ever placed in a request header and never logged.
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from .config import HISTORY_DAYS, SHANGHAI, MarketSpec
from .safety import api_key, sanitize

log = logging.getLogger("dashboard")

THS_BASE = "https://fuyao.aicubes.cn"
THS_PATH = "/api/fund/market/historical"
BAR_COLUMNS = ["Date", "open", "high", "low", "close", "adj_close", "adj_open", "volume"]
THROTTLE_S = 0.5


class FetchError(RuntimeError):
    pass


@dataclass
class FetchResult:
    market: str
    bars: dict
    rate: pd.Series
    sources: dict
    rate_source: str
    notes: list = field(default_factory=list)
    fetched_at_utc: str = ""
    extra_prices: dict = field(default_factory=dict)     # latest close of the cash-proxy ETF (order list only)

    @property
    def fallback_used(self) -> bool:
        return any(not s.startswith(("THS", "yfinance")) for s in self.sources.values()) or "快照" in self.rate_source


def retry(fn, tries: int = 3, wait: float = 2.0, throttle: float = THROTTLE_S, what: str = "request"):
    """At most `tries` attempts with a throttle before each call; raises FetchError (sanitized) at the end."""
    last = None
    for i in range(tries):
        try:
            time.sleep(throttle)
            return fn()
        except Exception as e:
            last = e
            log.warning("%s failed (attempt %d/%d): %s", what, i + 1, tries, sanitize(f"{type(e).__name__}: {e}"))
            if i < tries - 1:
                time.sleep(wait)
    raise FetchError(sanitize(f"{what}: {type(last).__name__}: {last}"))


def _check_bars(sym: str, df: pd.DataFrame) -> pd.DataFrame:
    if df is None or len(df) < 100:
        raise FetchError(f"{sym}: too few rows ({0 if df is None else len(df)})")
    df = df.sort_values("Date").drop_duplicates("Date", keep="last").reset_index(drop=True)
    for c in ("open", "high", "low", "close", "adj_close", "adj_open"):
        if df[c].isna().any() or not (df[c] > 0).all():
            raise FetchError(f"{sym}: non-positive or missing {c}")
    return df


# ------------------------------------------------------------------------------------------------ US
def fetch_us(spec: MarketSpec, now_utc: datetime, start: str = "2018-01-01") -> FetchResult:
    import yfinance as yf
    from paper_v2.market_data import normalize_yf_frame
    from research_v3.cash_yield import load_irx

    bars, sources = {}, {}
    for sym in spec.universe:
        raw = retry(lambda s=sym: yf.download(s, start=start, progress=False, auto_adjust=False, actions=True),
                    what=f"yfinance {sym}")
        if raw is None or len(raw) == 0:
            raise FetchError(f"yfinance returned no data for {sym}")
        bars[sym] = _check_bars(sym, normalize_yf_frame(raw))
        sources[sym] = "yfinance"
    end = (pd.Timestamp(now_utc).tz_localize(None) + pd.Timedelta(days=2)).strftime("%Y-%m-%d")

    def _irx():
        raw = yf.download("^IRX", start="2021-01-01", end=end, auto_adjust=False, progress=False)
        if raw is None or len(raw) == 0:
            raise FetchError("yfinance returned no ^IRX data")
        s = raw["Close"]["^IRX"] if isinstance(raw.columns, pd.MultiIndex) else raw["Close"]
        return pd.Series(s.values, index=pd.DatetimeIndex(s.index).strftime("%Y-%m-%d"), dtype=float).dropna()

    fwd = retry(_irx, what="yfinance ^IRX")
    base = load_irx()                               # hash-verified research snapshot, 2005..2020
    fwd = fwd[~fwd.index.isin(base.index)]
    rate = pd.concat([base, fwd]).sort_index()
    extra = {}
    try:
        from .config import load_account
        proxy = load_account()["US"]["cash_proxy"]
        raw = retry(lambda: yf.download(proxy, period="10d", progress=False, auto_adjust=False), what=f"yfinance {proxy}")
        c = raw["Close"][proxy] if isinstance(raw.columns, pd.MultiIndex) else raw["Close"]
        extra[proxy] = float(c.dropna().iloc[-1])
    except Exception as e:  # noqa: BLE001  (non-fatal: the order list falls back to a nominal price)
        log.warning("cash proxy price unavailable: %s", sanitize(e))
    return FetchResult("US", bars, rate, sources, "yfinance ^IRX(2021起)+研究数据(至2020)",
                       fetched_at_utc=now_utc.astimezone(timezone.utc).isoformat(), extra_prices=extra)


# ------------------------------------------------------------------------------------------------ CN
def _ths_code(sym: str) -> str:
    from research_v3.cn_transfer.data import SINA_PREFIX
    return f"{sym}.{'SH' if SINA_PREFIX[sym] == 'sh' else 'SZ'}"


def fetch_ths(sym: str, key: str, now_utc: datetime) -> pd.DataFrame:
    import requests
    end_ms = int(now_utc.timestamp() * 1000)
    start_ms = end_ms - HISTORY_DAYS * 86400 * 1000
    r = requests.get(THS_BASE + THS_PATH, headers={"X-api-key": key}, timeout=30,
                     params={"thscode": _ths_code(sym), "interval": "1d", "start": start_ms, "end": end_ms})
    r.raise_for_status()
    j = r.json()
    if j.get("code") != 0 or not (j.get("data") or {}).get("item"):
        raise FetchError(f"THS error code={j.get('code')} message={str(j.get('message'))[:120]}")
    df = pd.DataFrame(j["data"]["item"])
    df["Date"] = pd.to_datetime(df["date_ms"], unit="ms", utc=True).dt.tz_convert(SHANGHAI).dt.strftime("%Y-%m-%d")
    out = pd.DataFrame({"Date": df["Date"], "open": df["open_price"], "high": df["high_price"],
                        "low": df["low_price"], "close": df["close_price"], "adj_close": df["close_price"],
                        "adj_open": df["open_price"], "volume": df["volume"]})
    return out[BAR_COLUMNS]


def fetch_sina(sym: str) -> pd.DataFrame:
    import akshare as ak
    import requests
    from research_v3.cn_transfer.data import SINA_PREFIX, build_adjusted, parse_hfq_js
    sina = SINA_PREFIX[sym] + sym
    raw = retry(lambda: ak.fund_etf_hist_sina(symbol=sina), what=f"sina {sym}", throttle=1.5)
    raw = raw.rename(columns={"date": "Date"})
    raw["Date"] = pd.to_datetime(raw["Date"]).dt.strftime("%Y-%m-%d")
    raw = raw.sort_values("Date").reset_index(drop=True)
    r = retry(lambda: requests.get(f"https://finance.sina.com.cn/realstock/company/{sina}/hfq.js",
                                   headers={"User-Agent": "Mozilla/5.0"}, timeout=20),
              what=f"sina hfq {sym}", throttle=1.5)
    r.raise_for_status()
    cum_div, share_pts = parse_hfq_js(r.text)
    bars = build_adjusted(raw[["Date", "open", "high", "low", "close", "volume"]], cum_div, share_pts)
    cut = (pd.Timestamp.now() - pd.Timedelta(days=HISTORY_DAYS)).strftime("%Y-%m-%d")
    return bars[bars["Date"] >= cut][BAR_COLUMNS].reset_index(drop=True)


def fetch_eastmoney(sym: str) -> pd.DataFrame:
    import akshare as ak
    start = (pd.Timestamp.now() - pd.Timedelta(days=HISTORY_DAYS)).strftime("%Y%m%d")
    raw = retry(lambda: ak.fund_etf_hist_em(symbol=sym, period="daily", start_date=start,
                                            end_date=pd.Timestamp.now().strftime("%Y%m%d"), adjust="hfq"),
                what=f"eastmoney {sym}", throttle=1.5)
    df = pd.DataFrame({"Date": pd.to_datetime(raw["日期"]).dt.strftime("%Y-%m-%d"), "open": raw["开盘"].astype(float),
                       "high": raw["最高"].astype(float), "low": raw["最低"].astype(float),
                       "close": raw["收盘"].astype(float), "adj_close": raw["收盘"].astype(float),
                       "adj_open": raw["开盘"].astype(float), "volume": raw["成交量"].astype(float)})
    return df[BAR_COLUMNS]


def fetch_shibor() -> tuple[pd.Series, str, list]:
    import akshare as ak
    notes = []
    try:
        sh = retry(lambda: ak.rate_interbank(market="上海银行同业拆借市场", symbol="Shibor人民币", indicator="3月"),
                   what="akshare SHIBOR 3M", throttle=1.0)
        s = pd.Series(sh["利率"].astype(float).values, index=pd.to_datetime(sh["报告日"]).dt.strftime("%Y-%m-%d"),
                      dtype=float).sort_index()
        s = s[~s.index.duplicated(keep="last")].dropna()
        if len(s) < 500:
            raise FetchError("SHIBOR history too short")
        return s, "akshare rate_interbank SHIBOR 3月", notes
    except Exception as e:
        from research_v3.cn_transfer.data import load_shibor
        s = load_shibor()
        notes.append(sanitize(f"SHIBOR 下载失败，改用研究快照(至{s.index[-1]}): {e}"))
        return s, f"研究快照 SHIBOR 3月(至{s.index[-1]})", notes


def fetch_cn(spec: MarketSpec, now_utc: datetime) -> FetchResult:
    key = api_key()
    bars, sources, notes = {}, {}, []
    if not key:
        notes.append("未配置 FUYAO_API_KEY，THS 主源不可用，使用备用源")
    for sym in spec.universe:
        df, src = None, None
        if key:
            try:
                df = _check_bars(sym, retry(lambda s=sym: fetch_ths(s, key, now_utc), what=f"THS {sym}"))
                src = "THS"
            except Exception as e:
                notes.append(sanitize(f"{sym} THS 失败: {e}"))
        if df is None:
            try:
                df = _check_bars(sym, fetch_sina(sym))
                src = "新浪(自建复权)"
            except Exception as e:
                notes.append(sanitize(f"{sym} 新浪失败: {e}"))
        if df is None:
            try:
                df = _check_bars(sym, fetch_eastmoney(sym))
                src = "东方财富(后复权)"
            except Exception as e:
                notes.append(sanitize(f"{sym} 东方财富失败: {e}"))
                raise FetchError(sanitize(f"{sym}: THS、新浪、东方财富均失败"))
        bars[sym], sources[sym] = df, src
    rate, rate_src, n2 = fetch_shibor()
    extra = {}
    if key:
        try:
            from .config import load_account
            proxy = load_account()["CN"]["cash_proxy"]
            code = f"{proxy}.{'SZ' if proxy.startswith(('15', '16')) else 'SH'}"
            import requests
            end_ms = int(now_utc.timestamp() * 1000)

            def _px():
                r = requests.get(THS_BASE + THS_PATH, headers={"X-api-key": key}, timeout=30,
                                 params={"thscode": code, "interval": "1d", "start": end_ms - 20 * 86400 * 1000, "end": end_ms})
                r.raise_for_status()
                return r.json()["data"]["item"][-1]["close_price"]
            extra[proxy] = float(retry(_px, what=f"THS {proxy}"))
        except Exception as e:  # noqa: BLE001  (non-fatal)
            log.warning("cash proxy price unavailable: %s", sanitize(e))
    return FetchResult("CN", bars, rate, sources, rate_src, notes + n2,
                       fetched_at_utc=now_utc.astimezone(timezone.utc).isoformat(), extra_prices=extra)


# ------------------------------------------------------------------------------------------------ snapshots
def sha256_file(p: Path) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def write_snapshot(root: Path, date_str: str, fr: FetchResult) -> Path:
    """dashboard/data/<date>/<MARKET>/*.csv plus a sha256 manifest merged into dashboard/data/<date>/manifest.json."""
    d = Path(root) / date_str
    sub = d / fr.market
    sub.mkdir(parents=True, exist_ok=True)
    files = {}
    for sym, df in fr.bars.items():
        p = sub / f"{sym}.csv"
        df.to_csv(p, index=False)
        files[f"{fr.market}/{sym}.csv"] = {"sha256": sha256_file(p), "rows": int(len(df))}
    p = sub / "rate.csv"
    pd.DataFrame({"Date": list(fr.rate.index), "rate_pct": fr.rate.values}).to_csv(p, index=False)
    files[f"{fr.market}/rate.csv"] = {"sha256": sha256_file(p), "rows": int(len(fr.rate))}
    mp = d / "manifest.json"
    man = json.loads(mp.read_text()) if mp.is_file() else {"markets": {}}
    man["markets"][fr.market] = {"fetched_at_utc": fr.fetched_at_utc, "sources": fr.sources,
                                 "rate_source": fr.rate_source, "notes": fr.notes, "files": files,
                                 "extra_prices": fr.extra_prices}
    tmp = mp.with_suffix(".tmp")
    tmp.write_text(json.dumps(man, indent=2, sort_keys=True, ensure_ascii=False) + "\n")
    tmp.replace(mp)
    return d


def load_snapshot(snapshot_dir: Path, market: str) -> FetchResult:
    d = Path(snapshot_dir)
    man = json.loads((d / "manifest.json").read_text())["markets"][market]
    for rel, meta in man["files"].items():
        if sha256_file(d / rel) != meta["sha256"]:
            raise FetchError(f"snapshot hash mismatch: {rel}")
    bars = {}
    for rel in man["files"]:
        if rel.endswith("rate.csv"):
            continue
        bars[Path(rel).stem] = pd.read_csv(d / rel, dtype={"Date": str})
    r = pd.read_csv(d / market / "rate.csv", dtype={"Date": str})
    rate = pd.Series(r["rate_pct"].values, index=r["Date"].values, dtype=float)
    return FetchResult(market, bars, rate, man["sources"], man["rate_source"], man["notes"], man["fetched_at_utc"],
                       man.get("extra_prices", {}))
