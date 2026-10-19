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
    detail: dict = field(default_factory=dict)           # per-symbol source detail (attempts, splice junction/factor)

    @property
    def fallback_used(self) -> bool:
        return any(not s.startswith(("THS", "yfinance")) for s in self.sources.values()) or "快照" in self.rate_source


def retry(fn, tries: int = 3, wait: float = 2.0, throttle: float = THROTTLE_S, what: str = "request",
          backoff: float = 1.0):
    """At most `tries` attempts with a throttle before each call (sleep `wait*backoff**i` between attempts);
    raises FetchError (sanitized) at the end."""
    last = None
    for i in range(tries):
        try:
            time.sleep(throttle)
            return fn()
        except Exception as e:
            last = e
            log.warning("%s failed (attempt %d/%d): %s", what, i + 1, tries, sanitize(f"{type(e).__name__}: {e}"))
            if i < tries - 1:
                time.sleep(wait * backoff ** i)
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
YF_TRIES = 3
SOURCE_ALPACA = "Alpaca IEX"


def _yf_frame(yf, sym: str, **kw) -> pd.DataFrame:
    """One yfinance download; an EMPTY frame counts as a failure (yfinance sometimes returns empty, not an error)."""
    raw = yf.download(sym, progress=False, auto_adjust=False, **kw)
    if raw is None or len(raw) == 0:
        raise FetchError(f"yfinance returned no data for {sym}")
    return raw


def iter_us_snapshots(data_root: Path):
    """Yield (date_str, snapshot_dir, US manifest) newest first; unreadable manifests are skipped."""
    root = Path(data_root)
    if not root.is_dir():
        return
    for d in sorted((x for x in root.iterdir() if x.is_dir()), key=lambda x: x.name, reverse=True):
        try:
            man = json.loads((d / "manifest.json").read_text())["markets"]["US"]
        except Exception:  # noqa: BLE001
            continue
        yield d.name, d, man


def _verified_csv(d: Path, man: dict, rel: str):
    meta = man["files"].get(rel)
    if not meta or not (d / rel).is_file() or sha256_file(d / rel) != meta["sha256"]:
        return None
    return pd.read_csv(d / rel, dtype={"Date": str})


def load_cached_us_bars(data_root: Path, sym: str):
    """(snapshot date, bars) of the most recent snapshot whose `sym` bars came from yfinance and pass the hash check."""
    for date_str, d, man in iter_us_snapshots(data_root):
        if not str(man.get("sources", {}).get(sym, "")).startswith("yfinance"):
            continue
        df = _verified_csv(d, man, f"US/{sym}.csv")
        if df is not None and len(df):
            return date_str, df
    return None


def splice_bars(cached: pd.DataFrame, alp: pd.DataFrame, sym: str = "") -> tuple:
    """Cached yfinance history + Alpaca bars after the last cached date. The Alpaca adj_close/adj_open are scaled so
    the level is continuous at the junction date (which must exist in both): factor = cached adj_close / Alpaca
    adj_close there. Raw OHLC/close stay Alpaca raw. Returns (bars, junction_date, factor, n_new)."""
    cached = cached.sort_values("Date").reset_index(drop=True)
    j = str(cached["Date"].iloc[-1])
    a = alp.set_index("Date")
    if j not in a.index:
        raise FetchError(f"{sym}: junction date {j} missing in Alpaca bars")
    factor = float(cached["adj_close"].iloc[-1]) / float(a.loc[j, "adj_close"])
    if not (0.5 < factor < 2.0):
        raise FetchError(f"{sym}: implausible junction factor {factor:.4f} at {j}")
    new = alp[alp["Date"] > j].copy()
    new["adj_close"] = new["adj_close"] * factor
    new["adj_open"] = new["adj_open"] * factor
    if "dividends" in cached.columns:
        new["dividends"] = 0.0                      # not provided by the fallback; unused by the dashboard
    out = pd.concat([cached, new[list(cached.columns)]], ignore_index=True)
    return out, j, factor, int(len(new))


def _alpaca_us_bars(sym: str, start: str, data_root: Path, http=None):
    """Alpaca recent bars spliced onto the newest cached yfinance snapshot (raises FetchError if none)."""
    from . import iex_fallback as alpaca
    cached = load_cached_us_bars(data_root, sym)
    if cached is None:
        raise FetchError(f"{sym}: no cached yfinance snapshot to splice Alpaca bars onto")
    snap_date, cdf = cached
    try:
        alp = alpaca.fetch_alpaca_bars(sym, start=str(cdf["Date"].iloc[-1]), http=http)
    except alpaca.AlpacaError as e:
        raise FetchError(sanitize(f"{sym}: {e}")) from None
    out, j, factor, n_new = splice_bars(cdf, alp, sym)
    detail = {"source": f"{SOURCE_ALPACA}(拼接于快照 {snap_date})", "snapshot": snap_date, "junction": j,
              "factor": round(factor, 8), "new_bars": n_new}
    return out, detail


def _irx_from_snapshot(data_root: Path):
    for date_str, d, man in iter_us_snapshots(data_root):
        r = _verified_csv(d, man, "US/rate.csv")
        if r is not None and len(r):
            return date_str, pd.Series(r["rate_pct"].values, index=r["Date"].values, dtype=float)
    return None


def fetch_us(spec: MarketSpec, now_utc: datetime, start: str = "2018-01-01", data_root: Path | None = None,
             http=None) -> FetchResult:
    import yfinance as yf
    from paper_v2.market_data import normalize_yf_frame
    from research_v3.cash_yield import load_irx
    from . import config

    data_root = Path(data_root or config.DATA_ROOT)
    bars, sources, detail, notes = {}, {}, {}, []
    for sym in spec.universe:
        n_try = [0]

        def _one(s=sym, n=n_try):
            n[0] += 1
            return _yf_frame(yf, s, start=start, actions=True)
        try:
            raw = retry(_one, tries=YF_TRIES, what=f"yfinance {sym}", backoff=2.0)
            bars[sym] = _check_bars(sym, normalize_yf_frame(raw))
            sources[sym] = "yfinance"
            detail[sym] = {"source": "yfinance", "attempts": n_try[0]}
        except Exception as e_yf:  # noqa: BLE001
            log.warning("%s: yfinance exhausted (%s); falling back to Alpaca IEX", sym, sanitize(e_yf))
            try:
                df, d = _alpaca_us_bars(sym, start, data_root, http)
                bars[sym] = _check_bars(sym, df)
            except Exception as e_al:  # noqa: BLE001
                raise FetchError(sanitize(f"{sym}: yfinance 失败({e_yf}); Alpaca 备用也失败({e_al})")) from None
            d["yfinance_attempts"] = YF_TRIES
            sources[sym], detail[sym] = d["source"], d
            notes.append(sanitize(f"{sym} yfinance 连续 {YF_TRIES} 次失败，改用 Alpaca IEX 近期行情，"
                                  f"拼接于快照 {d['snapshot']}（交接日 {d['junction']}，复权系数 {d['factor']:.6f}，"
                                  f"新增 {d['new_bars']} 根）"))
    end = (pd.Timestamp(now_utc).tz_localize(None) + pd.Timedelta(days=2)).strftime("%Y-%m-%d")

    def _irx():
        raw = _yf_frame(yf, "^IRX", start="2021-01-01", end=end)
        s = raw["Close"]["^IRX"] if isinstance(raw.columns, pd.MultiIndex) else raw["Close"]
        return pd.Series(s.values, index=pd.DatetimeIndex(s.index).strftime("%Y-%m-%d"), dtype=float).dropna()

    rate_source = "yfinance ^IRX(2021起)+研究数据(至2020)"
    try:
        fwd = retry(_irx, tries=YF_TRIES, what="yfinance ^IRX", backoff=2.0)
        base = load_irx()                           # hash-verified research snapshot, 2005..2020
        fwd = fwd[~fwd.index.isin(base.index)]
        rate = pd.concat([base, fwd]).sort_index()
    except FetchError as e:
        snap = _irx_from_snapshot(data_root)
        if snap is None:
            raise
        rate_source = f"快照 {snap[0]} 的现金利率(^IRX 获取失败)"
        rate = snap[1]
        notes.append(sanitize(f"^IRX 获取失败，现金利率沿用快照 {snap[0]}（末日 {rate.index[-1]}）: {e}"))
    extra = {}
    try:
        from .config import load_account
        proxy = load_account()["US"]["cash_proxy"]
        try:
            raw = retry(lambda: _yf_frame(yf, proxy, period="10d"), tries=YF_TRIES, what=f"yfinance {proxy}", backoff=2.0)
            c = raw["Close"][proxy] if isinstance(raw.columns, pd.MultiIndex) else raw["Close"]
            extra[proxy] = float(c.dropna().iloc[-1])
        except Exception as e:  # noqa: BLE001
            log.warning("%s: yfinance exhausted (%s); falling back to Alpaca latest close", proxy, sanitize(e))
            from . import iex_fallback as alpaca
            extra[proxy] = alpaca.latest_close(proxy, http=http)
            notes.append(f"{proxy} 最新价来自 {SOURCE_ALPACA}（yfinance 失败）")
    except Exception as e:  # noqa: BLE001  (non-fatal: the order list falls back to a nominal price)
        log.warning("cash proxy price unavailable: %s", sanitize(e))
    return FetchResult("US", bars, rate, sources, rate_source, notes,
                       fetched_at_utc=now_utc.astimezone(timezone.utc).isoformat(), extra_prices=extra, detail=detail)


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
                                 "extra_prices": fr.extra_prices, "source_detail": fr.detail}
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
                       man.get("extra_prices", {}), man.get("source_detail", {}))
