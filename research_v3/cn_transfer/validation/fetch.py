"""One-time network fetch for the CN validation (Part A sources + B3 substitute ETFs), frozen with a sha256 manifest.

Throttle 1.5 s between requests, at most 3 tries per call, no long sleeps. Every call (success or failure) is logged
in data/fetch_log.json so unreachable sources are documented, never silently replaced.

Sources
  EM_HFQ / EM_UNADJ : ak.fund_etf_hist_em (adjust='hfq' / '')  [push2his.eastmoney.com]
  EM_NAV            : ak.fund_etf_fund_info_em
  EM_DIV            : fund.eastmoney.com Data/funddataIndex_Interface.aspx dt=8 (same endpoint as ak.fund_fh_em),
                      paged by fund code descending so the scan stops once codes drop below 159915
  EM_SPLIT          : same interface dt=9 (ak.fund_cf_em), same paging
  TENCENT_UNADJ     : ak.stock_zh_a_hist_tx unadjusted (independent price source used when EM_HFQ is unreachable)
  SINA (B3)         : ak.fund_etf_hist_sina + Sina hfq.js, exactly as research_v3.cn_transfer.data.create_snapshot
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pandas as pd

from ..data import BAR_COLUMNS, SINA_PREFIX, CNDataError, build_adjusted, parse_hfq_js, sha256_file

VDATA = Path(__file__).resolve().parent / "data"
MANIFEST = "manifest.json"
BASE_SYMBOLS = ["510300", "510050", "510500", "159915", "518880", "511010"]
B3_SYMBOLS = ["510330", "159922"]
SINA_PREFIX = {**SINA_PREFIX, "510330": "sh", "159922": "sz"}
EM_START, EM_END = "20130701", "20260930"
DIV_YEARS = list(range(2012, 2027))
FH_URL = "https://fund.eastmoney.com/Data/funddataIndex_Interface.aspx"
STOP_CODE = "159915"
KEEP_CODES = set(BASE_SYMBOLS) | set(B3_SYMBOLS)

LOG: list[dict] = []


def call(label: str, fn, tries: int = 3):
    """Throttled call, <=3 tries, logs outcome."""
    last = None
    for i in range(tries):
        time.sleep(1.5)
        try:
            out = fn()
            LOG.append({"call": label, "status": "OK", "tries": i + 1})
            return out
        except Exception as e:  # noqa: BLE001
            last = e
    LOG.append({"call": label, "status": "FAILED", "tries": tries, "error": repr(last)[:300]})
    return None


def parse_fh_text(text: str) -> tuple[list[list[str]], list[int]]:
    """Parse '[[...]]' js array + pageinfo = [pages, per_page, current]; robust (json, no eval)."""
    i = text.find("pageinfo")
    pageinfo = json.loads(text[text.find("[", i): text.find("]", i) + 1])
    a = text.find("=[[")
    if a < 0:
        return [], pageinfo
    start = a + 1
    end = text.find("];", start)
    return json.loads(text[start:end + 1]), pageinfo


def fetch_fh(dt: int, year: int, session) -> list[list[str]]:
    """All rows of the EM distribution (dt=8) / split (dt=9) tables for one year, scanning pages in code-descending
    order until the smallest code on a page is < STOP_CODE. Returns only rows for KEEP_CODES."""
    rows, page, pages = [], 1, 1
    while page <= pages:
        params = {"dt": str(dt), "page": str(page), "rank": "BZDM", "sort": "desc", "gs": "", "ftype": "", "year": str(year)}
        txt = call(f"EM_{'DIV' if dt == 8 else 'SPLIT'} year={year} page={page}",
                   lambda: session.get(FH_URL, params=params, timeout=20).text)
        if txt is None:
            raise CNDataError(f"EM {'DIV' if dt == 8 else 'SPLIT'} year {year} page {page} unreachable")
        data, info = parse_fh_text(txt)
        pages = info[0]
        if not data:
            break
        rows += [r for r in data if r[0] in KEEP_CODES]
        if min(r[0] for r in data) < STOP_CODE:
            break
        page += 1
    return rows


def run_fetch(data_dir: Path = VDATA) -> dict:
    import akshare as ak
    import requests

    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    files: list[str] = []
    status: dict[str, str] = {}

    # 1. EM_HFQ / EM_UNADJ attempts (protocol sources)
    for sym in BASE_SYMBOLS:
        for adj, tag in (("hfq", "EM_HFQ"), ("", "EM_UNADJ")):
            df = call(f"{tag} {sym}", lambda: ak.fund_etf_hist_em(symbol=sym, period="daily", start_date=EM_START,
                                                                    end_date=EM_END, adjust=adj))
            key = f"{tag}:{sym}"
            if df is not None and len(df):
                name = f"em_{'hfq' if adj else 'unadj'}_{sym}.csv"
                df.to_csv(data_dir / name, index=False)
                files.append(name)
                status[key] = "OK"
            else:
                status[key] = "UNREACHABLE" if df is None else "EMPTY"

    # 2. EM_NAV
    for sym in BASE_SYMBOLS:
        df = call(f"EM_NAV {sym}", lambda: ak.fund_etf_fund_info_em(fund=sym, start_date=EM_START, end_date=EM_END))
        if df is not None and len(df):
            name = f"em_nav_{sym}.csv"
            df.to_csv(data_dir / name, index=False)
            files.append(name)
            status[f"EM_NAV:{sym}"] = "OK"
        else:
            status[f"EM_NAV:{sym}"] = "UNREACHABLE" if df is None else "EMPTY"

    # 3. EM_DIV / EM_SPLIT
    sess = requests.Session()
    sess.headers.update({"User-Agent": "Mozilla/5.0"})
    for dt, name, cols in ((8, "em_div.csv", ["code", "name", "record_date", "ex_date", "amount_per_share", "pay_date", "n"]),
                           (9, "em_split.csv", None)):
        allrows, ok = [], True
        for y in DIV_YEARS:
            try:
                for r in fetch_fh(dt, y, sess):
                    allrows.append([y] + r)
            except CNDataError as e:
                ok = False
                status[f"EM_{'DIV' if dt == 8 else 'SPLIT'}"] = f"PARTIAL/UNREACHABLE: {e}"
                break
        if allrows or ok:
            width = max((len(r) for r in allrows), default=8)
            df = pd.DataFrame(allrows, columns=["query_year"] + (cols if cols and len(cols) + 1 == width else
                                                                 [f"c{i}" for i in range(width - 1)]))
            df.to_csv(data_dir / name, index=False)
            files.append(name)
            if ok:
                status[f"EM_{'DIV' if dt == 8 else 'SPLIT'}"] = "OK"

    # 4. Tencent unadjusted bars (independent price source)
    for sym in BASE_SYMBOLS:
        tsym = SINA_PREFIX[sym] + sym
        df = call(f"TENCENT_UNADJ {sym}", lambda: ak.stock_zh_a_hist_tx(symbol=tsym, start_date=EM_START, end_date=EM_END, adjust=""))
        if df is not None and len(df):
            df = df.copy()
            df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
            name = f"tencent_unadj_{sym}.csv"
            df.to_csv(data_dir / name, index=False)
            files.append(name)
            status[f"TENCENT_UNADJ:{sym}"] = "OK"
        else:
            status[f"TENCENT_UNADJ:{sym}"] = "UNREACHABLE" if df is None else "EMPTY"

    # 5. B3 substitutes through the baseline Sina path
    for sym in B3_SYMBOLS:
        sina = SINA_PREFIX[sym] + sym
        raw = call(f"SINA bars {sym}", lambda: ak.fund_etf_hist_sina(symbol=sina))
        r = call(f"SINA hfq.js {sym}", lambda: requests.get(f"https://finance.sina.com.cn/realstock/company/{sina}/hfq.js",
                                                            headers={"User-Agent": "Mozilla/5.0"}, timeout=20))
        if raw is None or r is None:
            status[f"SINA:{sym}"] = "UNREACHABLE"
            continue
        r.raise_for_status()
        raw = raw.rename(columns={"date": "Date"})
        raw["Date"] = pd.to_datetime(raw["Date"]).dt.strftime("%Y-%m-%d")
        raw = raw.sort_values("Date").reset_index(drop=True)
        raw.to_csv(data_dir / f"{sym}_sina_raw.csv", index=False)
        (data_dir / f"{sym}_sina_hfqjs_raw.json").write_text(r.text)
        files += [f"{sym}_sina_raw.csv", f"{sym}_sina_hfqjs_raw.json"]
        try:
            cum_div, share_pts = parse_hfq_js(r.text)
            bars = build_adjusted(raw[["Date", "open", "high", "low", "close", "volume"]], cum_div, share_pts)
            bars[BAR_COLUMNS].to_csv(data_dir / f"{sym}.csv", index=False)
            files.append(f"{sym}.csv")
            status[f"SINA:{sym}"] = f"OK first={raw['Date'].iloc[0]} last={raw['Date'].iloc[-1]} rows={len(raw)}"
        except Exception as e:  # noqa: BLE001
            status[f"SINA:{sym}"] = f"ADJUSTMENT FAILED: {e!r}"

    (data_dir / "fetch_log.json").write_text(json.dumps({"status": status, "calls": LOG,
                                                         "akshare_version": ak.__version__,
                                                         "fetched_utc": pd.Timestamp.now(tz="UTC").isoformat()}, indent=2))
    files.append("fetch_log.json")
    man = {"akshare_version": ak.__version__, "fetched_utc": pd.Timestamp.now(tz="UTC").isoformat(), "status": status,
           "files": {n: {"sha256": sha256_file(data_dir / n)} for n in sorted(set(files))}}
    (data_dir / MANIFEST).write_text(json.dumps(man, indent=2, sort_keys=True) + "\n")
    return man


def verify_manifest(data_dir: Path = VDATA) -> dict:
    data_dir = Path(data_dir)
    man = json.loads((data_dir / MANIFEST).read_text())
    for name, meta in man["files"].items():
        p = data_dir / name
        if not p.is_file():
            raise CNDataError(f"validation snapshot file missing: {name}")
        if sha256_file(p) != meta["sha256"]:
            raise CNDataError(f"validation snapshot hash mismatch for {name}")
    return man
