"""CLI: download price snapshot for the INCLUDED universe (read from the hash-verified classified list only),
510300 and SHIBOR 3M; write sha256 manifest. Run: python -m research_v3.sector_mom.fetch_data"""
from __future__ import annotations

import json
import re
import shutil

import pandas as pd

from research_v3.cn_transfer.data import verify_manifest as verify_cn_manifest

from . import universe_fetch
from .data import DATA_DIR, sector_snapshot, write_manifest
from .universe import load_included


def first_year_from_estab(s) -> int:
    m = re.search(r"(\d{4})年", str(s))
    return int(m.group(1)) if m else 2005


def main(log=print):
    inc = load_included()                         # classification is frozen and hashed BEFORE this runs
    tickers = [{"ticker": r.ticker, "exchange": r.exchange}
               for r in inc.itertuples()]
    tickers.append({"ticker": "510300", "exchange": "SH"})
    DATA_DIR.mkdir(exist_ok=True)
    info = sector_snapshot(tickers, universe_fetch._key(), DATA_DIR, log=log)
    # SHIBOR 3M: reuse the hash-verified cn_transfer snapshot (same akshare rate_interbank source)
    from research_v3.cn_transfer.data import DATA_DIR as CN_DIR
    verify_cn_manifest(CN_DIR)
    shutil.copyfile(CN_DIR / "shibor_3m.csv", DATA_DIR / "shibor_3m.csv")
    failed = [k for k, v in info.items() if v.get("status") != "ok"]
    src = pd.Series({k: v.get("source") for k, v in info.items()}).value_counts().to_dict()
    man = write_manifest(DATA_DIR, {"source_counts": src, "failed": failed,
                                    "note": "Bars are THS forward-adjusted (or flagged fallback); rows after 2024-09-30 are stored but never loaded by scoring code (guard).",
                                    "shibor": "copied from research_v3/cn_transfer/data/shibor_3m.csv (manifest-verified)"})
    log(json.dumps({"n_symbols": len(info), "source_counts": src, "failed": failed}))
    return man


if __name__ == "__main__":
    main()
