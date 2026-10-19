"""CN ETF + SHIBOR 3M data: one-time akshare/Sina download, frozen snapshot with sha256 manifest, verified loader.

Source change (implementation note, protocol names no vendor): Eastmoney (fund_etf_hist_em hfq) refused connections,
so bars come from Sina (ak.fund_etf_hist_sina, UNADJUSTED OHLC) and the back-adjustment is built here from Sina's
distribution data (hfq.js: cumulative cash dividend `u` and share-conversion factor `s`).
Adjustment (build_adjusted): cumulative total-return factor F_t = prod_{ex-dates <= t} M_d * (1 + D_d / close_d),
where D_d is the cash distribution per share at ex-date d (reinvested at the ex-date close) and M_d = s_after/s_before
is the share-conversion multiplier (price jumps by 1/M on a conversion). adj_open = open * F_t, adj_close = close * F_t
(same-day factor, the research_v2 adj_open convention).

Files in data/ (all hashed in manifest.json):
  {sym}_sina_raw.csv       Sina unadjusted bars
  {sym}_sina_hfqjs_raw.json  Sina hfq.js payload (distributions / share conversions)
  {sym}.csv                research_v2-compatible bars: Date, open, high, low, close (unadjusted), adj_close, adj_open,
                           volume
  shibor_3m.csv            Date, shibor_3m_pct   (akshare rate_interbank, Shibor人民币 3月, annual percent)
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import time
from pathlib import Path

import pandas as pd

DATA_DIR = Path(__file__).resolve().parent / "data"
MANIFEST = "manifest.json"
BAR_COLUMNS = ["Date", "open", "high", "low", "close", "adj_close", "adj_open", "volume"]
SINA_PREFIX = {"510300": "sh", "510050": "sh", "510500": "sh", "159915": "sz", "518880": "sh", "511010": "sh"}


class CNDataError(ValueError):
    pass


def sha256_file(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _retry(fn, tries: int = 3, wait: float = 2.0):
    last = None
    for i in range(tries):
        try:
            time.sleep(1.5)  # throttle
            return fn()
        except Exception as e:
            last = e
            time.sleep(wait)
    raise CNDataError(f"download failed after {tries} tries: {last!r}")


def parse_hfq_js(text: str) -> tuple[list[tuple[str, float]], list[tuple[str, float]]]:
    """Sina hfq.js -> (cumulative dividend points [(date, u)], share-factor points [(date, s)]), date-sorted,
    excluding the 1900-01-01 placeholder (which carries u=0, s=1)."""
    m = re.search(r"=\s*(\{.*?\})\s*(?:/\*|$)", text, flags=re.S)
    if not m:
        raise CNDataError("cannot parse hfq.js")
    data = json.loads(m.group(1))["data"]
    pts = sorted((r["d"], float(r["u"]), float(r["s"])) for r in data if r["d"] != "1900-01-01")
    return [(d, u) for d, u, _ in pts], [(d, s) for d, _, s in pts]


def build_adjusted(bars: pd.DataFrame, cum_div: list[tuple[str, float]], share_pts: list[tuple[str, float]]
                   ) -> pd.DataFrame:
    """Adds adj_open / adj_close (total-return back-adjustment, see module docstring) to unadjusted bars."""
    div = {}
    prev_u = 0.0
    for d, u in cum_div:
        inc = u - prev_u
        prev_u = u
        if abs(inc) > 1e-12:
            div[d] = inc
    mult = {}
    prev_s = 1.0
    for d, sv in share_pts:
        if abs(sv - prev_s) > 1e-12:
            mult[d] = sv / prev_s
        prev_s = sv
    dates = list(bars["Date"])
    close = dict(zip(bars["Date"], bars["close"]))
    for d in list(div) + list(mult):
        if d not in close:
            raise CNDataError(f"distribution/conversion date {d} has no bar")
    # Sina dates a share conversion at the record date; the price jump happens on that bar or one of the next two.
    # Apply M on the bar (within 3) whose close ratio best matches 1/M.
    aligned = {}
    for d, m in mult.items():
        i = dates.index(d)
        cands = [j for j in range(i, min(i + 3, len(dates))) if j > 0]
        j = min(cands, key=lambda k: abs(math.log(close[dates[k]] * m / close[dates[k - 1]])))
        aligned[dates[j]] = aligned.get(dates[j], 1.0) * m
    mult = aligned
    f = 1.0
    fac = []
    for d, c in zip(bars["Date"], bars["close"]):
        f *= mult.get(d, 1.0) * (1.0 + div.get(d, 0.0) / c)
        fac.append(f)
    out = bars.copy()
    out["adj_close"] = out["close"] * pd.Series(fac, index=out.index)
    out["adj_open"] = out["open"] * pd.Series(fac, index=out.index)
    return out


def create_snapshot(symbols, data_dir: Path = DATA_DIR) -> dict:
    """Download once (network) and freeze. Not used by tests or by the run command."""
    import akshare as ak
    import requests

    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    files = []
    events = {}
    for sym in symbols:
        sina = SINA_PREFIX[sym] + sym
        raw = _retry(lambda: ak.fund_etf_hist_sina(symbol=sina))
        raw = raw.rename(columns={"date": "Date"})
        raw["Date"] = pd.to_datetime(raw["Date"]).dt.strftime("%Y-%m-%d")
        raw = raw.sort_values("Date").reset_index(drop=True)
        raw.to_csv(data_dir / f"{sym}_sina_raw.csv", index=False)
        r = _retry(lambda: requests.get(f"https://finance.sina.com.cn/realstock/company/{sina}/hfq.js",
                                        headers={"User-Agent": "Mozilla/5.0"}, timeout=20))
        r.raise_for_status()
        (data_dir / f"{sym}_sina_hfqjs_raw.json").write_text(r.text)
        cum_div, share_pts = parse_hfq_js(r.text)
        bars = build_adjusted(raw[["Date", "open", "high", "low", "close", "volume"]], cum_div, share_pts)
        bars[BAR_COLUMNS].to_csv(data_dir / f"{sym}.csv", index=False)
        events[sym] = {"dividend_points": len(cum_div), "share_factor_points": len(share_pts)}
        files += [f"{sym}_sina_raw.csv", f"{sym}_sina_hfqjs_raw.json", f"{sym}.csv"]
    sh = _retry(lambda: ak.rate_interbank(market="上海银行同业拆借市场", symbol="Shibor人民币", indicator="3月"))
    sh = pd.DataFrame({"Date": pd.to_datetime(sh["报告日"]).dt.strftime("%Y-%m-%d"),
                       "shibor_3m_pct": sh["利率"].astype(float)}).sort_values("Date").reset_index(drop=True)
    sh.to_csv(data_dir / "shibor_3m.csv", index=False)
    files.append("shibor_3m.csv")
    try:  # optional cross-check source; recorded if reachable
        _retry(lambda: ak.macro_china_shibor_all()).to_csv(data_dir / "shibor_all_crosscheck_raw.csv", index=False)
        files.append("shibor_all_crosscheck_raw.csv")
    except CNDataError:
        pass
    manifest = {
        "source": "akshare fund_etf_hist_sina (unadjusted) + Sina hfq.js distributions (self-built total-return "
                  "adjustment); rate_interbank Shibor人民币 3月",
        "akshare_version": ak.__version__,
        "download_date": pd.Timestamp.now(tz="UTC").strftime("%Y-%m-%d"),
        "download_timestamp_utc": pd.Timestamp.now(tz="UTC").isoformat(),
        "symbols": list(symbols),
        "distribution_events": events,
        "price_note": "open/high/low/close = unadjusted exchange prices; adj_open/adj_close = self-built total-return adjusted",
        "files": {n: {"sha256": sha256_file(data_dir / n), "rows": int(len(pd.read_csv(data_dir / n)))
                      if n.endswith(".csv") else None} for n in sorted(files)},
    }
    (data_dir / MANIFEST).write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return manifest


def verify_manifest(data_dir: Path = DATA_DIR) -> dict:
    data_dir = Path(data_dir)
    manifest = json.loads((data_dir / MANIFEST).read_text())
    for name, meta in manifest["files"].items():
        p = data_dir / name
        if not p.is_file():
            raise CNDataError(f"snapshot file missing: {name}")
        actual = sha256_file(p)
        if actual != meta["sha256"]:
            raise CNDataError(f"snapshot hash mismatch for {name}: manifest {meta['sha256']} vs actual {actual}")
    return manifest


def load_bars(symbols, data_dir: Path = DATA_DIR) -> dict[str, pd.DataFrame]:
    """Hash-verified load of the v2-compatible bar files."""
    verify_manifest(data_dir)
    out = {}
    for sym in symbols:
        out[sym] = pd.read_csv(Path(data_dir) / f"{sym}.csv", dtype={"Date": str})
    return out


def load_shibor(data_dir: Path = DATA_DIR) -> pd.Series:
    """Hash-verified; Series indexed by 'YYYY-MM-DD' in annual percent."""
    verify_manifest(data_dir)
    df = pd.read_csv(Path(data_dir) / "shibor_3m.csv", dtype={"Date": str})
    if not df["Date"].is_unique:
        raise CNDataError("duplicate SHIBOR dates")
    return pd.Series(df["shibor_3m_pct"].values, index=df["Date"].values, dtype=float)


def validate_bars(bars: dict[str, pd.DataFrame], sessions: list[str]) -> dict:
    """Checks: unique dates, positive finite prices, OHLC sanity, bars only on XSHG sessions.
    Returns a report dict (hard problems are listed under 'errors'; missing in-sample bars are counted by caller)."""
    sess = set(sessions)
    rep = {"errors": [], "extra_session_bars": {}, "per_symbol": {}}
    for sym, df in bars.items():
        dates = df["Date"].tolist()
        if len(set(dates)) != len(dates):
            rep["errors"].append(f"{sym}: duplicate dates")
        if dates != sorted(dates):
            rep["errors"].append(f"{sym}: dates not sorted")
        for col in ("open", "close", "adj_open", "adj_close", "high", "low"):
            if (df[col].isna()).any() or not (df[col] > 0).all():
                rep["errors"].append(f"{sym}: non-positive/NaN {col}")
        if (df["high"] < df["low"]).any():
            rep["errors"].append(f"{sym}: high < low")
        extra = [d for d in dates if d not in sess]
        if extra:
            rep["extra_session_bars"][sym] = extra
        rep["per_symbol"][sym] = {"rows": len(df), "first": dates[0], "last": dates[-1]}
    return rep
