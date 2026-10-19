"""Loaders and dense panel for cb_rotation. Every loader clips rows to guard.current_limit() (DEV_END by default) at the
file boundary. The only information read from beyond the limit is one boolean per bond (`terminated`: the bond's
true last bar is on or before the limit), which fixes whether a bond that stops printing inside development has really
ended; no post-limit price, premium or date is ever returned.

Bond bars: Sina (OHLC + volume in bonds) merged with Tencent amount (CNY). Where Tencent has no row for a Sina bar the
amount is approximated as close x volume (the two agree to ~0.05% on overlapping bars); the count is reported.
Valuation: Eastmoney RPTA_WEB_KZZ_LS raw 15 columns, truncated at the bond's last bar.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from . import guard

HERE = Path(__file__).resolve().parent
DATA_DIR = HERE / "data"
PANEL_START = "2017-06-01"          # first panel session (20-session amount history before 2018-01-01)
NEVER = "9999-12-31"


def d10(x) -> str:
    return str(x)[:10]


# ------------------------------------------------------------------------------------------------ list
def read_list(data_dir: Path = DATA_DIR) -> pd.DataFrame:
    d = pd.read_csv(Path(data_dir) / "em_cb_list_full.csv", dtype=str)
    for c in ("LISTING_DATE", "DELIST_DATE", "NOTICE_DATE_SH", "NOTICE_DATE_HS", "EXPIRE_DATE", "VALUE_DATE", "CEASE_DATE"):
        d[c] = pd.Series([d10(x) if isinstance(x, str) and x.strip() and x.strip().lower() not in ("nan", "none", "nat") else None
                          for x in d[c]], index=d.index, dtype=object)
    return d


def contract_maturity(value_date, years) -> str | None:
    """Scheduled maturity = value (interest-start) date + bond term in years (known at issue)."""
    if not value_date or years is None or (isinstance(years, float) and np.isnan(years)):
        return None
    try:
        return (pd.Timestamp(value_date) + pd.DateOffset(years=int(float(years)))).strftime("%Y-%m-%d")
    except (ValueError, TypeError):
        return None


def bond_meta(lst: pd.DataFrame) -> pd.DataFrame:
    """One row per real bond (404xxx delisting-arrangement codes dropped) with the fields the engine needs."""
    d = lst[~lst["SECURITY_CODE"].str.startswith("404") & lst["LISTING_DATE"].notna()].copy()
    out = pd.DataFrame({
        "code": d["SECURITY_CODE"].values, "name": d["SECURITY_NAME_ABBR"].values,
        "stock": d["CONVERT_STOCK_CODE"].values, "listing": d["LISTING_DATE"].values,
        "delist": d["DELIST_DATE"].values, "rating": d["RATING"].values,
        "reason_sh": d["EXECUTE_REASON_SH"].values, "reason_hs": d["EXECUTE_REASON_HS"].values,
        "redeem_type": d["REDEEM_TYPE"].values,
        "price_sh": d["EXECUTE_PRICE_SH"].values, "price_hs": d["EXECUTE_PRICE_HS"].values,
        "expire_em": d["EXPIRE_DATE"].values,
    })
    nsh, nhs = d["NOTICE_DATE_SH"].values, d["NOTICE_DATE_HS"].values
    out["notice"] = [min([x for x in (a, b) if x], default=None) for a, b in zip(nsh, nhs)]
    out["maturity"] = [contract_maturity(v, y) for v, y in zip(d["VALUE_DATE"].values, d["BOND_EXPIRE"].values)]
    return out.reset_index(drop=True)


# ------------------------------------------------------------------------------------------------ bars / valuation
def true_last_bar(code: str, data_dir: Path = DATA_DIR) -> str | None:
    """Date of the last bar in the UNCLIPPED Sina file (used only for classification of delisted bonds, see
    classify.py, and for the `terminated` boolean)."""
    p = Path(data_dir) / "sina" / f"{code}.csv"
    if not p.exists():
        return None
    s = pd.read_csv(p, usecols=["date"], dtype=str)
    return d10(s["date"].max()) if len(s) else None


def load_bars(code: str, data_dir: Path = DATA_DIR, limit: str | None = None):
    """(bars DataFrame indexed by date with open high low close volume amount amount_approx, terminated) or (None, False)."""
    limit = limit or guard.current_limit()
    ps, pt = Path(data_dir) / "sina" / f"{code}.csv", Path(data_dir) / "tx" / f"{code}.csv"
    if not ps.exists():
        return None, False
    s = pd.read_csv(ps, dtype={"date": str})
    if not len(s):
        return None, False
    s["date"] = s["date"].map(d10)
    terminated = bool(s["date"].max() <= limit)
    s = guard.clip_to_dev(s, "date")
    s = s[(s["close"] > 0) & (s["open"] > 0)].drop_duplicates("date", keep="last").set_index("date").sort_index()
    if not len(s):
        return None, terminated
    amt = pd.Series(np.nan, index=s.index)
    if pt.exists():
        t = pd.read_csv(pt, dtype={"date": str})
        if len(t):
            t["date"] = t["date"].map(d10)
            t = guard.clip_to_dev(t, "date").drop_duplicates("date", keep="last").set_index("date")
            amt = t["amount"].reindex(s.index)
    approx = amt.isna()
    s["amount"] = amt.where(~approx, s["close"] * s["volume"])
    s["amount_approx"] = approx
    return s[["open", "high", "low", "close", "volume", "amount", "amount_approx"]], terminated


def load_va(code: str, last_bar: str | None, data_dir: Path = DATA_DIR, limit: str | None = None) -> pd.DataFrame | None:
    """Daily valuation (index date): swapvalue, swapor (percent), swapprice, truncated at the last bar and the limit."""
    limit = limit or guard.current_limit()
    p = Path(data_dir) / "va" / f"{code}.csv"
    if not p.exists():
        return None
    v = pd.read_csv(p)
    if not len(v):
        return None
    v["date"] = v["DATE"].map(d10)
    v = guard.clip_to_dev(v, "date").drop_duplicates("date", keep="last").set_index("date").sort_index()
    if last_bar:
        v = v[v.index <= last_bar]
    out = pd.DataFrame({"swapvalue": pd.to_numeric(v["SWAPVALUE"], errors="coerce"),
                        "swapor": pd.to_numeric(v["SWAPOR"], errors="coerce"),
                        "swapprice": pd.to_numeric(v["SWAPPRICE"], errors="coerce")})
    return out


def load_series_csv(path: Path, date_col: str, val_col: str) -> pd.Series:
    df = pd.read_csv(path, dtype={date_col: str})
    df[date_col] = df[date_col].map(d10)
    df = guard.clip_to_dev(df, date_col)
    return pd.Series(df[val_col].values.astype(float), index=df[date_col].values)


def load_cb_index(data_dir: Path = DATA_DIR) -> pd.Series:
    return load_series_csv(Path(data_dir) / "index" / "000832.csv", "date", "close")


def load_csi300() -> pd.Series:
    return load_series_csv(HERE.parent / "cn_transfer" / "data" / "510300.csv", "Date", "adj_close")


def load_shibor() -> pd.Series:
    return load_series_csv(HERE.parent / "cn_transfer" / "data" / "shibor_3m.csv", "Date", "shibor_3m_pct")


# ------------------------------------------------------------------------------------------------ panel
@dataclass
class CBPanel:
    codes: list
    names: list
    stocks: list                    # underlying stock code per bond
    sessions: list                  # 'YYYY-MM-DD'
    open: np.ndarray
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray               # NaN where no bar
    volume: np.ndarray              # bonds; NaN where no bar
    amount: np.ndarray              # CNY; NaN where no bar
    premium: np.ndarray             # decimal conversion premium on the session (NaN where unavailable)
    st: np.ndarray                  # underlying inferred-ST on the session (bool)
    notice: np.ndarray              # date str of first redemption notice (NEVER if none or after the limit)
    maturity: np.ndarray            # contract maturity date str (NEVER if unknown)
    bars_prior: np.ndarray          # bars before the panel's first session
    last_bar_idx: np.ndarray        # row of last bar (-1 none)
    first_bar_idx: np.ndarray
    terminated: np.ndarray          # bool: bond really stopped trading inside the loaded window
    delist_type: np.ndarray         # 'redemption' / 'maturity' / 'passive' / '' (for terminated bonds)
    extra: dict = field(default_factory=dict)
    # derived
    has_bar: np.ndarray = None
    close_ff: np.ndarray = None

    def finalize(self):
        self.has_bar = np.isfinite(self.close) & (np.nan_to_num(self.volume) > 0)
        self.close_ff = pd.DataFrame(self.close).ffill().to_numpy()
        return self

    @property
    def n_sessions(self):
        return len(self.sessions)


def build_panel(sessions: list, bonds: list, st_by_stock: dict, delist_types: dict,
                data_dir: Path = DATA_DIR, diag: dict | None = None) -> CBPanel:
    """bonds: list of meta dict rows (bond_meta records). st_by_stock: stock code -> bool array over `sessions`."""
    diag = diag if diag is not None else {}
    guard.assert_dates_allowed(sessions)
    S = len(sessions)
    sess_arr = np.array(sessions)
    cols = {k: [] for k in ("open", "high", "low", "close", "volume", "amount", "premium", "st")}
    codes, names, stocks, notice, mat = [], [], [], [], []
    prior, last_i, first_i, term, dtype = [], [], [], [], []
    n_approx = n_bars = n_nopremium = n_prem_fallback = n_nost = 0
    limit = guard.current_limit()
    for b in bonds:
        bars, terminated = load_bars(b["code"], data_dir)
        if bars is None:
            diag.setdefault("no_bars", []).append(b["code"])
            continue
        in_win = bars.index.isin(sess_arr)
        diag["bars_off_calendar"] = diag.get("bars_off_calendar", 0) + int((~in_win & (bars.index >= sessions[0])).sum())
        before = int((bars.index < sessions[0]).sum())
        w = bars[in_win]
        if not len(w):
            continue
        ri = np.searchsorted(sess_arr, w.index.values)
        A = {k: np.full(S, np.nan) for k in ("open", "high", "low", "close", "volume", "amount")}
        for k in A:
            A[k][ri] = w[k].to_numpy(float)
        n_approx += int(w["amount_approx"].sum())
        n_bars += len(w)
        va = load_va(b["code"], bars.index.max(), data_dir)
        prem = np.full(S, np.nan)
        if va is not None and len(va):
            vv = va[va.index.isin(sess_arr)]
            pos = np.searchsorted(sess_arr, vv.index.values)
            prem[pos] = vv["swapor"].to_numpy(float) / 100.0
            # fall back to close / conversion value - 1 where the premium is missing but the conversion value exists
            miss = ~np.isfinite(prem[pos]) & np.isfinite(vv["swapvalue"].to_numpy(float)) & np.isfinite(A["close"][pos]) \
                & (vv["swapvalue"].to_numpy(float) > 0)
            if miss.any():
                prem[pos[miss]] = A["close"][pos[miss]] / vv["swapvalue"].to_numpy(float)[miss] - 1.0
                n_prem_fallback += int(miss.sum())
        # premium only on bar sessions
        prem[~np.isfinite(A["close"])] = np.nan
        n_nopremium += int((np.isfinite(A["close"]) & ~np.isfinite(prem)).sum())
        for k in ("open", "high", "low", "close", "volume", "amount"):
            cols[k].append(A[k])
        cols["premium"].append(prem)
        sc = b["stock"]
        stm = st_by_stock.get(sc)
        if stm is None:
            n_nost += 1
            stm = np.zeros(S, bool)
        cols["st"].append(np.asarray(stm, bool))
        codes.append(b["code"]); names.append(b["name"]); stocks.append(sc)
        nt, mt = b.get("notice"), b.get("maturity")
        notice.append(nt if isinstance(nt, str) and nt <= limit else NEVER)
        mat.append(mt if isinstance(mt, str) else NEVER)
        prior.append(before)
        idx = np.where(np.isfinite(A["close"]))[0]
        first_i.append(int(idx.min())); last_i.append(int(idx.max()))
        term.append(bool(terminated and idx.max() < S - 1))
        dtype.append(delist_types.get(b["code"], "") if (terminated and idx.max() < S - 1) else "")
    diag.update(bars_total=n_bars, amount_approx_bars=n_approx, premium_fallback=n_prem_fallback,
                bar_sessions_without_premium=n_nopremium, bonds_without_stock_st=n_nost)
    st_arr = np.array(cols["st"]).T if cols["st"] else np.zeros((S, 0), bool)
    m = lambda k: np.array(cols[k]).T
    p = CBPanel(codes, names, stocks, list(sessions), m("open"), m("high"), m("low"), m("close"), m("volume"),
                m("amount"), m("premium"), st_arr, np.array(notice), np.array(mat), np.array(prior),
                np.array(last_i), np.array(first_i), np.array(term), np.array(dtype))
    return p.finalize()
