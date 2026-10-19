"""Step 1a: candidate ETF list + tracked-index profiles (NO price data is touched here).

Sources
  * THS /api/meta/tickers/list asset_type=fund-etf (key from .env FUYAO_API_KEY, never printed): current listed SH/SZ ETFs.
    The THS code table has no delisted ETFs (end_date/last_trade_date null for all rows, list_date mostly null).
  * akshare fund_name_em: extra ETF-like codes (51/52/56/58/15 prefix, 'ETF' in the name, not feeder) that are
    NOT in the THS table (possible delisted/merged or very new); kept as candidates, flagged source=akshare_only.
  * akshare fund_overview_em (Eastmoney) per code: tracked index (跟踪标的), benchmark, full name, inception date.
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent
UDIR = HERE / "universe"
BASE = "https://fuyao.aicubes.cn"


def _key() -> str:
    from dotenv import dotenv_values
    root = HERE.parents[1]
    k = dotenv_values(root / ".env").get("FUYAO_API_KEY") or os.environ.get("FUYAO_API_KEY")
    if not k:
        raise RuntimeError("FUYAO_API_KEY missing")
    return k


def fetch_ths_etf_list(throttle: float = 0.5) -> list[dict]:
    H = {"X-api-key": _key()}
    items, off = [], 0
    while True:
        r = requests.get(BASE + "/api/meta/tickers/list", params={"asset_type": "fund-etf", "limit": 1000, "offset": off},
                         headers=H, timeout=60).json()
        if r["code"] != 0:
            raise RuntimeError(f"THS list error code {r['code']}")
        it = r["data"]["item"]
        items += it
        if len(it) < 1000:
            break
        off += 1000
        time.sleep(throttle)
    return items


def akshare_only_candidates(ths_tickers: set[str]) -> list[dict]:
    import akshare as ak
    fn = ak.fund_name_em()
    m = fn[fn["基金简称"].str.contains("ETF") & ~fn["基金简称"].str.contains("联接")
           & fn["基金代码"].str.match(r"^(51|52|56|58|15)\d{4}$")]
    out = []
    for _, r in m.iterrows():
        if r["基金代码"] not in ths_tickers:
            c = r["基金代码"]
            out.append({"ticker": c, "thscode": c + (".SH" if c[0] in "56" or c[:2] in ("51", "52", "58") else ".SZ"),
                        "name": r["基金简称"], "exchange": "SH" if c[0] in "56" or c[:2] in ("51", "52", "58") else "SZ",
                        "source": "akshare_only"})
    return out


def fetch_profiles(tickers: list[str], path: Path, throttle: float = 0.35, tries: int = 3) -> None:
    import akshare as ak
    done = {}
    if path.exists():
        for ln in path.read_text().splitlines():
            d = json.loads(ln)
            done[d["ticker"]] = d
    with path.open("a") as f:
        for t in tickers:
            if t in done and not done[t].get("error"):
                continue
            rec = {"ticker": t}
            for k in range(tries):
                try:
                    df = ak.fund_overview_em(symbol=t)
                    rec.update({c: (None if str(df[c].iloc[0]) in ("nan", "None") else str(df[c].iloc[0])) for c in df.columns})
                    rec.pop("error", None)
                    break
                except Exception as e:  # noqa: BLE001
                    rec["error"] = f"{type(e).__name__}: {str(e)[:120]}"
                    time.sleep(2.0 * (k + 1))
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            f.flush()
            time.sleep(throttle)


if __name__ == "__main__":
    UDIR.mkdir(exist_ok=True)
    ths = fetch_ths_etf_list()
    extra = akshare_only_candidates({i["ticker"] for i in ths})
    (UDIR / "raw_ths_etf_list.json").write_text(json.dumps(ths, ensure_ascii=False, indent=1))
    (UDIR / "raw_akshare_only_candidates.json").write_text(json.dumps(extra, ensure_ascii=False, indent=1))
    tickers = sorted({i["ticker"] for i in ths} | {i["ticker"] for i in extra})
    print(len(ths), len(extra), len(tickers), flush=True)
    fetch_profiles(tickers, UDIR / "raw_profiles.jsonl")
    print("done", flush=True)
