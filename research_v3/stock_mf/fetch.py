"""Resumable, checkpointed raw-data download for stock_mf (data stays local; sha256 manifest in data/manifest.json).

Layout under data/:
  lists/ths_tickers.csv         THS meta a-share list (listed only)
  lists/delist_sh_sz.csv        akshare SH+SZ delisted list (code,name,list,delist,ex)
  dumps/daily_k.parquet         THS market dump (unadjusted OHLCV+amount, 2016-10-18 ..)
  dumps/adjustment_factors.parquet
  ths_early/{thscode}.csv       THS per-stock unadjusted bars 2008-01-01 .. 2016-12-31 (overlap with dump for checking)
  tx/{code}_hfq.csv, tx/{code}_raw.csv   Tencent bars for delisted stocks (back-adjusted / unadjusted)
  gbjg/{code}.csv               Eastmoney share-structure history
  index/{code}.csv              benchmark indices
Throttle: >= 0.5 s between THS calls, >= 1 s between akshare calls, at most 3 retries per call. The API key is read from
.env (FUYAO_API_KEY) and never printed or logged. Data after 2024-03-31 may be stored here; scoring code never reads it
(see guard.py). Run:  python -m research_v3.stock_mf.fetch
"""
from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
DATA = Path(__file__).resolve().parent / "data"
LOG = ROOT / "logs" / "stock_mf_fetch.log"
BASE = "https://fuyao.aicubes.cn"
THS_GAP, AK_GAP, RETRIES = 0.5, 1.0, 3
EARLY_START, EARLY_END = "2008-01-01", "2016-12-31"
TX_START, TX_END = "20070101", "20261231"

log = logging.getLogger("stock_mf_fetch")


class FetchError(RuntimeError):
    pass


class Throttle:
    def __init__(self, gap):
        self.gap, self.last, self.lock = gap, 0.0, threading.Lock()

    def wait(self):
        with self.lock:
            w = self.gap - (time.time() - self.last)
            if w > 0:
                time.sleep(w)
            self.last = time.time()


_ths_t, _ak_t = Throttle(THS_GAP), Throttle(AK_GAP)
_key = None


def _api_key() -> str:
    global _key
    if _key is None:
        from dotenv import dotenv_values
        _key = dotenv_values(ROOT / ".env")["FUYAO_API_KEY"]
    return _key


def _scrub(s: str) -> str:
    return s.replace(_api_key(), "***") if _key else s


def ms(date: str) -> int:
    return int(pd.Timestamp(date, tz="Asia/Shanghai").timestamp() * 1000)


def date_from_ms(x) -> str:
    return (pd.to_datetime(x, unit="ms") + pd.Timedelta(hours=8)).strftime("%Y-%m-%d")


def ths_get(path, params=None, raw=False):
    import requests
    last = None
    for i in range(RETRIES + 1):
        _ths_t.wait()
        try:
            r = requests.get(BASE + path, params=params, headers={"X-api-key": _api_key()}, timeout=90)
            if r.status_code in (429, 500, 502, 503):
                last = f"HTTP {r.status_code}"
                time.sleep(1.5 * (i + 1))
                continue
            if raw:
                return r
            j = r.json()
            if j.get("code") in (4001, 429):
                last = f"code {j.get('code')}"  # shared 'global' limiter: ~1/3 of calls are rejected at 0.6 s spacing
                time.sleep(1.5 * (i + 1))
                continue
            return j
        except Exception as e:  # noqa: BLE001
            last = _scrub(repr(e))[:200]
            time.sleep(2)
    raise FetchError(f"THS {path} failed after {RETRIES} retries: {last}")


def ak_call(fn, **kw):
    last = None
    for i in range(RETRIES + 1):
        _ak_t.wait()
        try:
            return fn(**kw)
        except Exception as e:  # noqa: BLE001
            last = repr(e)[:200]
            time.sleep(2 * (i + 1))
    raise FetchError(f"akshare {getattr(fn, '__name__', fn)} {kw} failed after {RETRIES} retries: {last}")


# ---------------------------------------------------------------------------------------------------------- tasks
def want_code(code: str, ex: str) -> bool:
    """SSE main/STAR, SZSE main/ChiNext A shares only (no BSE, no B shares)."""
    if ex == "SH":
        return code.startswith(("60", "68"))
    if ex == "SZ":
        return code.startswith(("00", "30"))
    return False


def fetch_lists():
    d = DATA / "lists"
    d.mkdir(parents=True, exist_ok=True)
    p = d / "ths_tickers.csv"
    if not p.exists():
        rows, off = [], 0
        while True:
            r = ths_get("/api/meta/tickers/list", {"asset_type": "a-share", "limit": 10000, "offset": off})
            it = r["data"]["item"]
            rows += it
            if len(it) < 10000:
                break
            off += 10000
        pd.DataFrame(rows).to_csv(p, index=False)
        log.info("ths ticker list: %d rows", len(rows))
    p2 = d / "delist_sh_sz.csv"
    if not p2.exists():
        import akshare as ak
        sh = ak_call(ak.stock_info_sh_delist)
        sz = ak_call(ak.stock_info_sz_delist)
        a = pd.DataFrame({"code": sh["公司代码"].astype(str).str.zfill(6), "name": sh["公司简称"],
                          "list": sh["上市日期"].astype(str), "delist": sh["暂停上市日期"].astype(str), "ex": "SH"})
        b = pd.DataFrame({"code": sz["证券代码"].astype(str).str.zfill(6), "name": sz["证券简称"],
                          "list": sz["上市日期"].astype(str), "delist": sz["终止上市日期"].astype(str), "ex": "SZ"})
        pd.concat([a, b]).to_csv(p2, index=False)
        log.info("delist list: %d rows", len(a) + len(b))
    return pd.read_csv(p, dtype=str), pd.read_csv(p2, dtype=str)


def fetch_dumps():
    import requests
    d = DATA / "dumps"
    d.mkdir(parents=True, exist_ok=True)
    for name, fn in [("daily-k", "daily_k.parquet"), ("adjustment-factors", "adjustment_factors.parquet")]:
        p = d / fn
        if p.exists():
            continue
        j = ths_get(f"/api/dump/market-dumps/{name}/download-url")
        u = j["data"]["presigned_url"]
        t = time.time()
        r = requests.get(u, timeout=600)
        r.raise_for_status()
        tmp = p.with_suffix(".tmp")
        tmp.write_bytes(r.content)
        tmp.rename(p)
        log.info("dump %s: %.1f MB in %.0fs", name, len(r.content) / 1e6, time.time() - t)


def _early_bars(thscode, start=EARLY_START, end=EARLY_END):
    j = ths_get("/api/a-share/prices/historical",
                {"thscode": thscode, "interval": "1d", "start": ms(start), "end": ms(end), "adjust": "none"})
    if j.get("code") != 0:
        raise FetchError(f"THS bars {thscode}: code {j.get('code')} {j.get('message')}")
    it = (j.get("data") or {}).get("item") or []
    df = pd.DataFrame(it)
    if df.empty:
        return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume", "amount"])
    df["date"] = df["date_ms"].map(date_from_ms)
    return df.rename(columns={"open_price": "open", "high_price": "high", "low_price": "low", "close_price": "close",
                              "turnover": "amount"})[["date", "open", "high", "low", "close", "volume", "amount"]]


def ths_worker(tick: pd.DataFrame, fails: dict):
    d = DATA / "ths_early"
    d.mkdir(parents=True, exist_ok=True)
    t = tick[[want_code(c, e) for c, e in zip(tick["ticker"], tick["exchange"])]]
    t = t[(t["list_date"].fillna("9999") <= EARLY_END)]
    todo = [c for c in t["thscode"] if not (d / f"{c}.csv").exists()]
    log.info("THS early: %d stocks to fetch (%d already)", len(todo), len(t) - len(todo))
    for i, c in enumerate(todo):
        try:
            _early_bars(c).to_csv(d / f"{c}.csv.tmp", index=False)
            (d / f"{c}.csv.tmp").rename(d / f"{c}.csv")
            fails.pop(f"ths:{c}", None)
        except Exception as e:  # noqa: BLE001
            fails[f"ths:{c}"] = _scrub(str(e))[:200]
            log.warning("THS early %s failed: %s", c, _scrub(str(e))[:200])
        if (i + 1) % 200 == 0:
            log.info("THS early progress %d/%d", i + 1, len(todo))
    log.info("THS early done")


def fetch_index(fails):
    """CSI1000 / CSI500: THS index endpoint over successive 10-year windows; Sina (akshare) as the fallback."""
    d = DATA / "index"
    d.mkdir(parents=True, exist_ok=True)
    for code in ["000852", "000905"]:
        p = d / f"{code}.csv"
        if p.exists():
            continue
        rows = []
        try:
            for s, e in [("2004-12-01", "2013-12-31"), ("2014-01-01", "2023-12-31"), ("2024-01-01", "2026-12-31")]:
                j = ths_get("/api/a-share-index/prices/historical",
                            {"thscode": f"{code}.SH", "interval": "1d", "start": ms(s), "end": ms(e)})
                rows += (j.get("data") or {}).get("item") or []
            df = pd.DataFrame(rows)
            if not df.empty:
                df["date"] = df["date_ms"].map(date_from_ms)
                df = df.rename(columns={"open_price": "open", "high_price": "high", "low_price": "low",
                                        "close_price": "close", "turnover": "amount"})
                df = df[["date", "open", "high", "low", "close", "volume", "amount"]].drop_duplicates("date")
                df["source"] = "ths"
        except Exception as e:  # noqa: BLE001
            df = pd.DataFrame()
            fails[f"index_ths:{code}"] = _scrub(str(e))[:200]
        import akshare as ak
        try:
            s = ak_call(ak.stock_zh_index_daily, symbol=f"sh{code}")
            s = s.rename(columns={"date": "date"})
            s["date"] = s["date"].astype(str).str[:10]
            s = s[["date", "open", "high", "low", "close"]]
            s["source"] = "sina"
            if df.empty:
                df = s
            else:  # THS first; Sina only for earlier dates THS lacks
                df = pd.concat([df, s[s["date"] < df["date"].min()]]).sort_values("date")
        except Exception as e:  # noqa: BLE001
            fails[f"index_sina:{code}"] = _scrub(str(e))[:200]
        if not df.empty:
            df.sort_values("date").to_csv(p, index=False)
            log.info("index %s: %d rows %s..%s", code, len(df), df["date"].min(), df["date"].max())


def ak_worker(tick, delist, fails, kinds=("tx", "gbjg"), shard=(0, 1)):
    import akshare as ak
    (DATA / "tx").mkdir(parents=True, exist_ok=True)
    (DATA / "gbjg").mkdir(parents=True, exist_ok=True)
    listed = set(tick["ticker"])
    dl = delist[[want_code(c, e) for c, e in zip(delist["code"], delist["ex"])]]
    dl = dl[(dl["delist"] >= "2008-01-01") & (~dl["code"].isin(listed))].drop_duplicates("code")
    log.info("delisted stocks to fetch (tx): %d", len(dl))
    jobs = []
    for _, r in dl.iterrows():
        jobs.append(("tx", r["code"], r["ex"]))
    for _, r in tick[[want_code(c, e) for c, e in zip(tick["ticker"], tick["exchange"])]].iterrows():
        if (r["list_date"] if isinstance(r["list_date"], str) else "9999") <= "2024-03-31":
            jobs.append(("gbjg", r["ticker"], r["exchange"]))
    for _, r in dl.iterrows():
        jobs.append(("gbjg", r["code"], r["ex"]))
    jobs = [j for j in jobs if j[0] in kinds][shard[0]::shard[1]]
    for i, (kind, code, ex) in enumerate(jobs):
        key = f"{kind}:{code}"
        try:
            if kind == "tx":
                sym = f"{ex.lower()}{code}"
                for adj, suf in [("hfq", "hfq"), ("", "raw")]:
                    p = DATA / "tx" / f"{code}_{suf}.csv"
                    if p.exists():
                        continue
                    df = ak_call(ak.stock_zh_a_hist_tx, symbol=sym, start_date=TX_START, end_date=TX_END, adjust=adj)
                    df["date"] = df["date"].astype(str).str[:10]
                    df.to_csv(str(p) + ".tmp", index=False)
                    Path(str(p) + ".tmp").rename(p)
            else:
                p = DATA / "gbjg" / f"{code}.csv"
                if p.exists():
                    continue
                df = ak_call(ak.stock_zh_a_gbjg_em, symbol=f"{code}.{ex}")
                df.to_csv(str(p) + ".tmp", index=False)
                Path(str(p) + ".tmp").rename(p)
            fails.pop(key, None)
        except Exception as e:  # noqa: BLE001
            fails[key] = str(e)[:200]
            log.warning("%s failed: %s", key, str(e)[:200])
        if (i + 1) % 100 == 0:
            log.info("akshare progress %d/%d", i + 1, len(jobs))
    log.info("akshare done")


def _atomic(p, text):
    tmp = Path(str(p) + ".tmp")
    tmp.write_text(text)
    tmp.rename(p)


def write_manifest():
    files = {}
    for p in sorted(DATA.rglob("*")):
        if p.is_file() and p.name not in ("manifest.json", "failures.json") and not p.name.endswith(".tmp"):
            h = hashlib.sha256()
            with open(p, "rb") as f:
                for chunk in iter(lambda: f.read(1 << 20), b""):
                    h.update(chunk)
            files[str(p.relative_to(DATA))] = {"sha256": h.hexdigest(), "bytes": p.stat().st_size}
    (DATA / "manifest.json").write_text(json.dumps(
        {"created": time.strftime("%Y-%m-%d %H:%M:%S"), "n_files": len(files), "files": files}, indent=0))
    log.info("manifest: %d files", len(files))


def main():
    LOG.parent.mkdir(exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s",
                        handlers=[logging.FileHandler(LOG), logging.StreamHandler()])
    DATA.mkdir(exist_ok=True)
    fails: dict = {}
    fp = DATA / "failures.json"
    tick, delist = fetch_lists()
    for attempt in range(3):  # stage-level retry (rate limits are dynamic); everything is resumable
        try:
            fetch_dumps()
            break
        except Exception as e:  # noqa: BLE001
            log.warning("dumps attempt %d failed: %s", attempt, _scrub(str(e))[:200])
            time.sleep(120)

    def ths_all():
        for _ in range(12):  # passes: later passes only retry what is still missing
            fetch_index(fails)
            ths_worker(tick, fails)
            if not any(k.startswith(("ths:", "index_ths")) for k in fails):
                break
            time.sleep(20)

    def ak_all(kind, shard=(0, 1)):  # several threads share the global >= 1 s akshare call-start throttle
        for _ in range(3):
            ak_worker(tick, delist, fails, (kind,), shard)
            if not any(k.startswith(kind + ":") for k in fails):
                break
            time.sleep(60)

    th_a = threading.Thread(target=ths_all)
    th_b = threading.Thread(target=ak_all, args=("tx",))
    th_cs = [threading.Thread(target=ak_all, args=("gbjg", (k, 1))) for k in range(1)]
    th_a.start()
    th_b.start()
    for t_ in th_cs:
        t_.start()
    while th_a.is_alive() or th_b.is_alive() or any(t_.is_alive() for t_ in th_cs):
        time.sleep(30)
        _atomic(fp, json.dumps(fails, ensure_ascii=False, indent=1))
    _atomic(fp, json.dumps(fails, ensure_ascii=False, indent=1))
    write_manifest()
    log.info("DONE failures=%d", len(fails))


if __name__ == "__main__":
    main()
