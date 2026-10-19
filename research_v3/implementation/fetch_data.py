"""One-off snapshot of the raw data used by the implementation checks (network; not used by tests).

Writes CSV/JSON under research_v3/implementation/data/ and a sha256 manifest.json (committed; CSVs are gitignored).
THS key: environment only, never logged.
"""
from __future__ import annotations

import hashlib
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from .ths import fund_bars

DATA = Path(__file__).resolve().parent / "data"
END = pd.Timestamp("2026-10-20")
HIST = 1820

CASH_CN = ["511880", "511990"]
BOND_CAND = ["511010", "511020", "511260", "511090", "511130", "511100", "511160", "511580", "511520", "511060",
             "159972", "511270", "159649", "511220"]
SINA_LONG = CASH_CN + BOND_CAND            # Sina unadjusted history (listing date) + hfq.js
US_CASH = ["BIL", "SGOV"]


def pre(sym):
    return "sz" if sym.startswith("159") else "sh"


def thscode(sym):
    return f"{sym}.{'SZ' if sym.startswith('159') else 'SH'}"


def retry(fn, tries=3, wait=2.0, throttle=1.0):
    last = None
    for i in range(tries):
        try:
            time.sleep(throttle)
            return fn()
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(wait)
    raise RuntimeError(f"failed after {tries} tries: {type(last).__name__}: {str(last)[:150]}")


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def write_manifest(extra_log: dict | None = None, extra_sources: dict | None = None):
    mp = DATA / "manifest.json"
    man = json.loads(mp.read_text()) if mp.is_file() else {}
    man.setdefault("fetch_log", {}).update(extra_log or {})
    man.setdefault("sources", {}).update(extra_sources or {})
    man["files"] = {p.name: {"sha256": sha(p), "bytes": p.stat().st_size} for p in sorted(DATA.glob("*"))
                    if p.name != "manifest.json"}
    mp.write_text(json.dumps(man, indent=2, sort_keys=True, ensure_ascii=False) + "\n")


def fetch_extra():
    """511990 pays daily cash income (price stays ~100): fetch the per-10k-unit income history instead."""
    import akshare as ak
    d = retry(lambda: ak.fund_money_fund_info_em(symbol="511990"), throttle=1.5)
    d.to_csv(DATA / "em_moneyfund_511990.csv", index=False)
    write_manifest({"em_moneyfund_511990": "ok"},
                   {"em_moneyfund_511990.csv": "akshare fund_money_fund_info_em 511990 (income per 10k units, 7d yield)"})


def main():
    import akshare as ak
    import requests
    import yfinance as yf
    DATA.mkdir(parents=True, exist_ok=True)
    log = {}
    for sym in CASH_CN + BOND_CAND:
        try:
            df = fund_bars(thscode(sym), END - pd.Timedelta(days=HIST), END)
            df.to_csv(DATA / f"ths_{sym}.csv", index=False)
            log[f"ths_{sym}.csv"] = "ok"
        except Exception as e:  # noqa: BLE001
            log[f"ths_{sym}.csv"] = f"FAILED {str(e)[:100]}"
    for sym in SINA_LONG:
        try:
            raw = retry(lambda s=sym: ak.fund_etf_hist_sina(symbol=pre(s) + s), throttle=1.5)
            raw = raw.rename(columns={"date": "Date"})
            raw["Date"] = pd.to_datetime(raw["Date"]).dt.strftime("%Y-%m-%d")
            raw.sort_values("Date").to_csv(DATA / f"sina_{sym}.csv", index=False)
            r = retry(lambda s=sym: requests.get(f"https://finance.sina.com.cn/realstock/company/{pre(s)}{s}/hfq.js",
                                                 headers={"User-Agent": "Mozilla/5.0"}, timeout=20), throttle=1.5)
            r.raise_for_status()
            (DATA / f"sina_{sym}_hfq.json").write_text(r.text)
            log[f"sina_{sym}"] = "ok"
        except Exception as e:  # noqa: BLE001
            log[f"sina_{sym}"] = f"FAILED {str(e)[:100]}"
    try:
        d = retry(lambda: ak.bond_zh_hs_daily(symbol="sh204001"), throttle=1.5)
        d.to_csv(DATA / "gc001_204001_sina.csv", index=False)
        log["gc001"] = "ok"
    except Exception as e:  # noqa: BLE001
        log["gc001"] = f"FAILED {str(e)[:100]}"
    try:
        d = retry(lambda: ak.repo_rate_hist(start_date="20160101", end_date="20261020"), throttle=1.5)
        d.to_csv(DATA / "fr001_chinamoney.csv", index=False)
        log["fr001"] = "ok"
    except Exception as e:  # noqa: BLE001
        log["fr001"] = f"FAILED {str(e)[:100]}"
    for t, start in [("BIL", "2007-05-01"), ("SGOV", "2020-05-01"), ("^IRX", "2007-01-01")]:
        try:
            raw = retry(lambda t=t, s=start: yf.download(t, start=s, end="2026-10-21", auto_adjust=False,
                                                         progress=False, actions=True))
            if isinstance(raw.columns, pd.MultiIndex):
                raw.columns = [c[0] for c in raw.columns]
            raw.index = pd.DatetimeIndex(raw.index).strftime("%Y-%m-%d")
            raw.index.name = "Date"
            raw.to_csv(DATA / f"yf_{t.strip('^')}.csv")
            log[f"yf_{t}"] = "ok"
        except Exception as e:  # noqa: BLE001
            log[f"yf_{t}"] = f"FAILED {str(e)[:100]}"
    files = {p.name: {"sha256": sha(p), "bytes": p.stat().st_size} for p in sorted(DATA.glob("*"))
             if p.name != "manifest.json"}
    man = {"created_utc": datetime.now(timezone.utc).isoformat(), "end_requested": str(END.date()), "fetch_log": log,
           "sources": {"ths_*.csv": "THS fuyao /api/fund/market/historical (forward-adjusted, 1d)",
                       "sina_*": "akshare fund_etf_hist_sina (unadjusted) + Sina hfq.js distributions",
                       "gc001_204001_sina.csv": "akshare bond_zh_hs_daily sh204001 (close = annualized rate %)",
                       "fr001_chinamoney.csv": "akshare repo_rate_hist (chinamoney FR/FDR fixings)",
                       "yf_*.csv": "yfinance, auto_adjust=False, actions=True"},
           "files": files}
    (DATA / "manifest.json").write_text(json.dumps(man, indent=2, sort_keys=True, ensure_ascii=False) + "\n")
    print(json.dumps(log, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    sys.exit(fetch_extra() if sys.argv[1:] == ["extra"] else main())
