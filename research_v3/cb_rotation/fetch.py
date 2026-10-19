"""Resumable, checkpointed raw-data download for cb_rotation (data stays local in data/, sha256 manifest in data/manifest.json).

Layout under data/:
  em_cb_list_full.csv      Eastmoney RPT_BOND_CB_LIST, all pages (all bonds incl. delisted)
  va/{code}.csv            Eastmoney RPTA_WEB_KZZ_LS raw 15-column daily valuation series (requested directly because
                           akshare bond_zh_cov_value_analysis breaks on the changed column count)
  sina/{code}.csv         Sina bond_zh_hs_cov_daily bars (OHLC + volume; no amount)
  tx/{code}.csv           Tencent bars (OHLC + volume + amount)
  index/000832.csv        CSI Convertible Bond Index (Tencent index daily)
Single sequential fetch loop, global throttle >= 1 s between any two remote calls, at most 3 retries per call.
404xxx delisting-arrangement codes have no bars and are skipped. Data after 2023-12-31 may be stored here; scoring code
never reads it (see guard.py). Run:  python -m research_v3.cb_rotation.fetch
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
DATA = Path(__file__).resolve().parent / "data"
LOG = ROOT / "logs" / "cb_rotation_fetch.log"
GAP, RETRIES = 1.0, 3
DATA_START = "2017-07-01"

log = logging.getLogger("cb_rotation_fetch")


class FetchError(RuntimeError):
    pass


_last = [0.0]


def call(fn, *a, **kw):
    last = None
    for i in range(RETRIES + 1):
        w = GAP - (time.time() - _last[0])
        if w > 0:
            time.sleep(w)
        _last[0] = time.time()
        try:
            r = fn(*a, **kw)
            _last[0] = time.time()
            return r
        except Exception as e:  # noqa: BLE001
            last = repr(e)[:200]
            time.sleep(2 * (i + 1))
    raise FetchError(f"{getattr(fn, '__name__', fn)} failed after {RETRIES} retries: {last}")


def em_list_page(page: int) -> list:
    import requests
    params = {"sortColumns": "PUBLIC_START_DATE", "sortTypes": "-1", "pageSize": "500", "pageNumber": str(page),
              "reportName": "RPT_BOND_CB_LIST", "columns": "ALL",
              "quoteColumns": "f2~01~CONVERT_STOCK_CODE~CONVERT_STOCK_PRICE,"
                              "f235~10~SECURITY_CODE~TRANSFER_PRICE,f236~10~SECURITY_CODE~TRANSFER_VALUE,"
                              "f2~10~SECURITY_CODE~CURRENT_BOND_PRICE,f237~10~SECURITY_CODE~TRANSFER_PREMIUM_RATIO,"
                              "f239~10~SECURITY_CODE~RESALE_TRIG_PRICE,f240~10~SECURITY_CODE~REDEEM_TRIG_PRICE,"
                              "f23~01~CONVERT_STOCK_CODE~PBV_RATIO", "source": "WEB", "client": "WEB"}
    r = requests.get("https://datacenter-web.eastmoney.com/api/data/v1/get", params=params, timeout=60)
    j = r.json()["result"]
    return j["pages"], j["data"]


def em_va(code: str) -> pd.DataFrame:
    import requests
    params = {"sty": "ALL", "token": "894050c76af8597a853f5b408b759f5d", "st": "date", "sr": "1", "source": "WEB",
              "type": "RPTA_WEB_KZZ_LS", "filter": f'(zcode="{code}")', "p": "1", "ps": "8000"}
    r = requests.get("https://datacenter-web.eastmoney.com/api/data/get", params=params, timeout=60)
    j = r.json()
    res = j.get("result")
    if not res or not res.get("data"):
        return pd.DataFrame()
    return pd.DataFrame(res["data"])


def prefix(code: str) -> str:
    return "sh" if code.startswith(("11", "13")) else "sz"


def _atomic_csv(df: pd.DataFrame, p: Path):
    tmp = Path(str(p) + ".tmp")
    df.to_csv(tmp, index=False)
    tmp.rename(p)


def fetch_list():
    p = DATA / "em_cb_list_full.csv"
    if p.exists():
        return pd.read_csv(p, dtype=str)
    pages, rows, pg = 1, [], 1
    while pg <= pages:
        pages, data = call(em_list_page, pg)
        rows += data
        pg += 1
    df = pd.DataFrame(rows)
    _atomic_csv(df, p)
    log.info("list: %d rows", len(df))
    return df.astype(str).replace("None", pd.NA)


def need_codes(lst: pd.DataFrame) -> list:
    """Bonds with a listing date, real (non-404) code, not delisted before DATA_START. Phase 1 = listed through
    2023-12-31 (development), phase 2 = listed afterwards (stored only)."""
    d = lst.copy()
    d["lst"] = d["LISTING_DATE"].astype(str).str[:10]
    d["dl"] = d["DELIST_DATE"].astype(str).str[:10]
    d = d[d["lst"].str.match(r"\d{4}-\d{2}-\d{2}") & ~d["SECURITY_CODE"].str.startswith("404")]
    d = d[(~d["dl"].str.match(r"\d{4}")) | (d["dl"] >= DATA_START)]
    d = d[d["lst"] <= time.strftime("%Y-%m-%d")]
    d = d.sort_values(["lst", "SECURITY_CODE"])
    p1 = d[d["lst"] <= "2023-12-31"]["SECURITY_CODE"].tolist()
    p2 = d[d["lst"] > "2023-12-31"]["SECURITY_CODE"].tolist()
    return p1 + p2


def fetch_one(code: str, fails: dict):
    import akshare as ak
    for sub in ("va", "sina", "tx"):
        p = DATA / sub / f"{code}.csv"
        if p.exists():
            continue
        key = f"{sub}:{code}"
        try:
            if sub == "va":
                df = call(em_va, code)
            elif sub == "sina":
                df = call(ak.bond_zh_hs_cov_daily, symbol=prefix(code) + code)
                df["date"] = df["date"].astype(str).str[:10]
            else:
                df = call(ak.stock_zh_a_hist_tx, symbol=prefix(code) + code, start_date="20170101",
                          end_date="20261231", adjust="")
                df["date"] = df["date"].astype(str).str[:10]
            _atomic_csv(df, p)
            fails.pop(key, None)
        except Exception as e:  # noqa: BLE001
            fails[key] = str(e)[:200]
            log.warning("%s failed: %s", key, str(e)[:200])


def fetch_index():
    import akshare as ak
    p = DATA / "index" / "000832.csv"
    if p.exists():
        return
    df = call(ak.stock_zh_index_daily_tx, symbol="sh000832")
    df["date"] = df["date"].astype(str).str[:10]
    _atomic_csv(df, p)
    log.info("index 000832: %d rows", len(df))


def write_manifest():
    files = {}
    for p in sorted(DATA.rglob("*")):
        if p.is_file() and p.name not in ("manifest.json", "failures.json") and not p.name.endswith(".tmp"):
            files[str(p.relative_to(DATA))] = {"sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
                                               "bytes": p.stat().st_size}
    (DATA / "manifest.json").write_text(json.dumps({"created": time.strftime("%Y-%m-%d %H:%M:%S"),
                                                    "n_files": len(files), "files": files}, indent=0))
    log.info("manifest: %d files", len(files))


def main():
    LOG.parent.mkdir(exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s",
                        handlers=[logging.FileHandler(LOG), logging.StreamHandler()])
    for s in ("va", "sina", "tx", "index"):
        (DATA / s).mkdir(parents=True, exist_ok=True)
    fp = DATA / "failures.json"
    fails = json.loads(fp.read_text()) if fp.exists() else {}
    lst = fetch_list()
    fetch_index()
    codes = need_codes(lst)
    log.info("bonds to fetch: %d", len(codes))
    for rnd in range(2):          # second pass retries only what is still missing
        for i, c in enumerate(codes):
            fetch_one(c, fails)
            if (i + 1) % 50 == 0:
                log.info("pass %d progress %d/%d failures=%d", rnd, i + 1, len(codes), len(fails))
                fp.write_text(json.dumps(fails, ensure_ascii=False, indent=1))
        if not fails:
            break
        time.sleep(30)
    fp.write_text(json.dumps(fails, ensure_ascii=False, indent=1))
    write_manifest()
    log.info("DONE failures=%d", len(fails))


if __name__ == "__main__":
    main()
