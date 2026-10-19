"""Delisting classification of convertible bonds into 'redemption', 'maturity' or 'passive'.

Rule (deterministic, no price information used):
  1. a 404xxx delisting-arrangement code exists for the same underlying stock          -> passive (underlying delisting)
  2. last bar within [0, MAT_LAG_DAYS] calendar days before the SCHEDULED maturity
     (value date + term; known at issue)                                               -> maturity
  3. a redemption notice (Eastmoney NOTICE_DATE_SH/HS) dated on or before the last bar  -> redemption
     (rule label 'notice_then_last_bar' when the gap is <= NOTICE_LAG_DAYS, else 'stale_notice': Eastmoney's notice date
     is sometimes much earlier than the real announcement, so these are the cases the cninfo check looks at hardest)
  4. otherwise (default, unexplained)                                                  -> passive
Bonds whose Eastmoney code says 'redemption/maturity' but which do not satisfy 2 or 3 fall to rule 4. The rule is
verified against cninfo announcements in verify_delist.py (sample of each type, every ambiguous bond). A reviewed override
table (`delist_overrides.json`: code -> {type, evidence}) may reassign a bond; the table is applied last and every
override is reported.
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from .data import DATA_DIR, true_last_bar

MAT_LAG_DAYS = 30
NOTICE_LAG_DAYS = 75
HERE = Path(__file__).resolve().parent
OVERRIDES = HERE / "delist_overrides.json"


def core(name: str) -> str:
    """Bond short name without the 转债 / 转退 / 退债 suffix (links a bond to its 404xxx delisting-arrangement code)."""
    import re
    return re.sub(r"(转债|转退|退债)$", "", str(name))


def days(a: str, b: str) -> int:
    return (pd.Timestamp(a) - pd.Timestamp(b)).days


def classify_one(last_bar: str, notice: str | None, maturity: str | None, has_404: bool) -> tuple[str, str]:
    notice = notice if isinstance(notice, str) else None
    maturity = maturity if isinstance(maturity, str) else None
    if has_404:
        return "passive", "404_arrangement"
    gap = days(last_bar, notice) if notice else None
    if gap is not None and 0 <= gap <= NOTICE_LAG_DAYS:
        return "redemption", "notice_then_last_bar"
    if maturity and 0 <= days(maturity, last_bar) <= MAT_LAG_DAYS:
        return "maturity", "last_bar_before_scheduled_maturity"
    if gap is not None and gap > NOTICE_LAG_DAYS:
        return "redemption", "stale_notice"
    return "passive", "unexplained"


def load_overrides(path: Path = OVERRIDES) -> dict:
    if Path(path).exists():
        return json.loads(Path(path).read_text())
    return {}


def classify_all(lst: pd.DataFrame, meta: pd.DataFrame, data_dir: Path = DATA_DIR, as_of: str = "2026-10-01"
                 ) -> pd.DataFrame:
    """One row per bond that has stopped trading (last bar before `as_of`). Uses the unclipped bar files (classification
    is a data-verification step, not a scoring step; no returns are computed from post-development data)."""
    r404 = lst[lst["SECURITY_CODE"].str.startswith("404")]
    stocks_404 = set(r404["CONVERT_STOCK_CODE"].dropna())
    cores_404 = {core(n) for n in r404["SECURITY_NAME_ABBR"].dropna()}
    ov = load_overrides()
    rows = []
    for r in meta.itertuples():
        lb = true_last_bar(r.code, data_dir)
        if lb is None or lb >= as_of:
            continue
        typ, rule = classify_one(lb, r.notice, r.maturity, r.stock in stocks_404 or core(r.name) in cores_404)
        o = ov.get(r.code)
        rows.append(dict(code=r.code, name=r.name, stock=r.stock, listing=r.listing, last_bar=lb, delist_date=r.delist,
                         notice=r.notice, maturity=r.maturity, rating=r.rating, reason_sh=r.reason_sh,
                         reason_hs=r.reason_hs, rule_type=typ, rule=rule, type=o["type"] if o else typ,
                         override=o["evidence"] if o else ""))
    return pd.DataFrame(rows)
