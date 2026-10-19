"""Panel building for stock_mf: dense (sessions x stocks) arrays of raw and adjusted prices, volume, float shares,
inferred-ST flags, ex-right reference prices, board / lot metadata.

Adjustment (listed stocks, THS): back-adjusted TOTAL-RETURN prices from the THS adjustment events. At ex-date d with
previous close P, cash dividend D, bonus shares B per share, rights ratio A at price K, the ex-right reference price is
R = (P - D + A*K) / (1 + B + A) and the event multiplier is M = P / R (rights ignored when K >= P). adj = raw * F with
F_t = prod_{ex <= t} M (same-day factor, so adj_open = open * F_t). Dividends are therefore reinvested at the ex-date
reference (declared simplification). Delisted stocks use Tencent hfq prices directly (adj_open = hfq open etc.).

Every loader clips rows to guard.current_limit() (DEV_END by default) at the file boundary.
"""
from __future__ import annotations

import gzip
import io
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from . import guard

DATA_DIR = Path(__file__).resolve().parent / "data"
DATA_START = "2008-01-01"
MIN_STOCKS_PER_SESSION = 100
ST_WINDOW = 60
EX_NOISE_TOL = 0.04          # Tencent-derived ex-right ratio is only used when it deviates by more than this


class PanelError(ValueError):
    pass


@dataclass
class Panel:
    codes: list
    ex: np.ndarray                  # 'SH' / 'SZ'
    sessions: list                  # 'YYYY-MM-DD'
    open: np.ndarray
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray               # raw (unadjusted), NaN where no bar
    volume: np.ndarray              # shares, NaN where no bar
    amount: np.ndarray              # CNY, NaN where no bar
    adj_open: np.ndarray            # NaN where no bar
    adj_close: np.ndarray           # NaN where no bar
    float_sh: np.ndarray            # as-of float A shares (NaN if unknown)
    exr: np.ndarray                 # ex-right ratio F_{t-1}/F_t (1 when no event)
    delisted: np.ndarray            # bool: stock stopped trading permanently inside the loaded window
    last_bar_idx: np.ndarray        # int, index of last bar session (-1 if none)
    first_bar_idx: np.ndarray
    sources: np.ndarray = None      # 'ths' / 'tx'
    # derived (filled by finalize())
    has_bar: np.ndarray = field(default=None)
    adj_close_ff: np.ndarray = field(default=None)
    close_ff: np.ndarray = field(default=None)
    ref_close: np.ndarray = field(default=None)
    st: np.ndarray = field(default=None)
    board: np.ndarray = field(default=None)
    lot: np.ndarray = field(default=None)
    bars_before: np.ndarray = field(default=None)

    def finalize(self):
        """Derived arrays. All causal (row t uses rows <= t only)."""
        self.has_bar = np.isfinite(self.close) & (np.nan_to_num(self.volume) > 0)
        self.adj_close_ff = ffill0(self.adj_close)
        self.close_ff = ffill0(self.close)
        prev = np.vstack([np.full((1, self.close.shape[1]), np.nan), self.close_ff[:-1]])
        self.ref_close = prev * self.exr
        self.st = infer_st(self.close, self.high, self.low, self.adj_close)
        n = self.close.shape[1]
        self.board = np.array([board_of(c, e) for c, e in zip(self.codes, self.ex)])
        self.lot = np.where(self.board == "STAR", 200, 100)
        cb = np.cumsum(np.isfinite(self.close), axis=0)
        self.bars_before = np.vstack([np.zeros((1, n), dtype=cb.dtype), cb[:-1]])   # bars strictly before row t
        return self

    @property
    def n_sessions(self):
        return len(self.sessions)

    def truncated(self, last_idx: int) -> "Panel":
        """Copy of the panel containing sessions 0..last_idx only (used by look-ahead tests)."""
        k = last_idx + 1
        kw = {}
        for f_ in ("open", "high", "low", "close", "volume", "amount", "adj_open", "adj_close", "float_sh", "exr"):
            kw[f_] = getattr(self, f_)[:k].copy()
        lb = np.where(self.last_bar_idx > last_idx, -1, self.last_bar_idx)
        p = Panel(self.codes, self.ex, self.sessions[:k], delisted=self.delisted & (self.last_bar_idx <= last_idx),
                  last_bar_idx=lb, first_bar_idx=self.first_bar_idx, sources=self.sources, **kw)
        return p.finalize()


def board_of(code: str, ex: str) -> str:
    if ex == "SH":
        return "STAR" if code.startswith("68") else "MAIN"
    return "CHINEXT" if code.startswith("30") else "MAIN"


def ffill0(a: np.ndarray) -> np.ndarray:
    """Forward fill along axis 0 (NaN until the first value)."""
    df = pd.DataFrame(a)
    return df.ffill().to_numpy()


def infer_st(close, high, low, adj_close, window: int = ST_WINDOW) -> np.ndarray:
    """Protocol inferred-ST: flagged on a date if within the last `window` sessions (including the date) the stock has
    at least one session closing at a +/-5% limit (|ret| in [4.85%, 5.15%], close == high for up, close == low for down)
    and no session with |ret| > 5.5%. ret = adjusted close / previous available adjusted close - 1."""
    close = np.asarray(close, float)
    adjc = np.asarray(adj_close, float)
    prev = np.vstack([np.full((1, close.shape[1]), np.nan), ffill0(adjc)[:-1]])
    with np.errstate(invalid="ignore", divide="ignore"):
        r = adjc / prev - 1.0
    a = np.abs(r)
    up = (r > 0) & np.isclose(close, high, rtol=0, atol=1e-6)
    dn = (r < 0) & np.isclose(close, low, rtol=0, atol=1e-6)
    lim = (a >= 0.0485) & (a <= 0.0515) & (up | dn)
    big = a > 0.055
    z = np.zeros((1, close.shape[1]))
    cl = np.vstack([z, np.cumsum(np.nan_to_num(lim.astype(float)), axis=0)])
    cb = np.vstack([z, np.cumsum(np.nan_to_num(big.astype(float)), axis=0)])
    S = close.shape[0]
    hi = np.arange(1, S + 1)
    lo = np.maximum(hi - window, 0)
    nl = cl[hi] - cl[lo]
    nb = cb[hi] - cb[lo]
    return (nl >= 1) & (nb == 0)


# ------------------------------------------------------------------------------------------------ adjustment
def event_multipliers(dates: np.ndarray, closes: np.ndarray, events: pd.DataFrame) -> tuple[np.ndarray, np.ndarray, int]:
    """Cumulative factor F (same length as dates) and per-row ex-right ratio exr = F_{t-1}/F_t from THS events.
    `dates` sorted str array of the stock's bar dates, `closes` its raw closes; events columns ex, dividend_per_share,
    per_share_bonus, allotment_ratio, allotment_price. Returns (F, exr, n_skipped)."""
    n = len(dates)
    F = np.ones(n)
    exr = np.ones(n)
    skipped = 0
    for _, e in events.sort_values("ex").iterrows():
        idx = int(np.searchsorted(dates, e["ex"], side="left"))
        if idx == 0 or idx >= n:
            continue          # before the first bar (no history to adjust) or after the last bar
        P = closes[idx - 1]
        D, B = float(e["dividend_per_share"]), float(e["per_share_bonus"])
        A, K = float(e["allotment_ratio"]), float(e["allotment_price"])
        if not np.isfinite(P) or P <= 0:
            skipped += 1
            continue
        if A > 0 and K >= P:
            A = 0.0
        R = (P - D + A * K) / (1.0 + B + A)
        if not (R > 0):
            skipped += 1
            continue
        M = P / R
        if not (0.05 < M < 50):
            skipped += 1
            continue
        F[idx:] *= M
        exr[idx] /= M
    return F, exr, skipped



# ------------------------------------------------------------------------------------------------ Tencent hfq -> total return
def tx_total_return_prices(raw_close, hfq_close, raw_open, n_large: int = 20):
    """Total-return adjusted close/open for a delisted stock from Tencent raw and 'hfq' prices.

    Tencent's hfq is NOT multiplicative: hfq = S * raw + D (S = cumulative bonus/split multiplier, D = cumulative cash
    dividends added back, in hfq units), so hfq daily returns are diluted by D. The holder's one-day total return is
    exactly  dh_t / (S_{t-1} * raw_{t-1})  with dh_t = hfq_t - hfq_{t-1}. On days with no corporate action this equals the
    raw return, which is used directly; on action days the formula above is used with S estimated as the median of
    dh/dr (dr = raw change) over the next `n_large` large-move sessions. Returns (adj_close, adj_open, n_action_days,
    n_S_changes). adj_open = adj_close * open/close (same-day relative)."""
    r = np.asarray(raw_close, float)
    h = np.asarray(hfq_close, float)
    o = np.asarray(raw_open, float)
    n = len(r)
    dr, dh = np.diff(r), np.diff(h)
    rp = r[:-1]
    large = np.abs(dr) >= np.maximum(0.03, 0.01 * rp)
    k = np.full(n - 1, np.nan)
    k[large] = dh[large] / dr[large]

    def s_forward(t, fallback):
        idx = np.where(large[t:])[0][:n_large]
        if len(idx) < 3:
            return fallback
        return float(np.median(k[t + idx]))

    S = s_forward(0, h[0] / r[0] if r[0] > 0 else 1.0)
    ret = np.zeros(n - 1)
    n_act = n_chg = 0
    for t in range(n - 1):
        tol = 0.011 + 0.011 * S
        if abs(dh[t] - S * dr[t]) <= tol:
            ret[t] = dr[t] / rp[t]
        else:
            n_act += 1
            ret[t] = dh[t] / (S * rp[t])
            S_new = s_forward(t + 1, S)
            if abs(S_new / S - 1) > 0.02:
                n_chg += 1
            S = S_new
    adj_close = np.concatenate([[r[0]], r[0] * np.cumprod(1.0 + ret)])
    adj_open = adj_close * o / r
    return adj_close, adj_open, n_act, n_chg

# ------------------------------------------------------------------------------------------------ assembling
@dataclass
class StockBars:
    """One stock's bars as plain arrays (the loader and the tests both build these)."""
    code: str
    ex: str
    df: pd.DataFrame                # index = date str; columns open high low close volume amount adj_open adj_close
    exr: pd.Series                  # index = date str (subset ok), ex-right ratio F_{t-1}/F_t
    float_records: pd.Series        # index = effective date str, values = float A shares
    delisted: bool
    source: str = "ths"


def assemble(stocks: list, sessions: list | None = None) -> Panel:
    if sessions is None:
        cnt = {}
        for s in stocks:
            for d in s.df.index:
                cnt[d] = cnt.get(d, 0) + 1
        thr = min(MIN_STOCKS_PER_SESSION, max(1, len(stocks)))
        sessions = sorted(d for d, c in cnt.items() if c >= thr)
    sess = np.array(sessions)
    S, N = len(sess), len(stocks)
    names = ("open", "high", "low", "close", "volume", "amount", "adj_open", "adj_close")
    arr = {k: np.full((S, N), np.nan) for k in names}
    fsh = np.full((S, N), np.nan)
    exr = np.ones((S, N))
    last_idx = np.full(N, -1)
    first_idx = np.full(N, -1)
    for j, s in enumerate(stocks):
        d = s.df
        d = d[d.index.isin(sess)]
        if d.empty:
            continue
        ri = np.searchsorted(sess, d.index.values)
        for k in names:
            arr[k][ri, j] = d[k].to_numpy(float)
        first_idx[j], last_idx[j] = ri.min(), ri.max()
        if len(s.exr):
            e = s.exr[s.exr.index.isin(sess)]
            exr[np.searchsorted(sess, e.index.values), j] = e.to_numpy(float)
        fr = s.float_records.dropna().sort_index()
        if len(fr):
            pos = np.searchsorted(fr.index.values, sess, side="right") - 1
            v = np.where(pos >= 0, fr.to_numpy(float)[np.maximum(pos, 0)], np.nan)
            fsh[:, j] = v
    codes = [s.code for s in stocks]
    p = Panel(codes, np.array([s.ex for s in stocks]), list(sessions), arr["open"], arr["high"], arr["low"],
              arr["close"], arr["volume"], arr["amount"], arr["adj_open"], arr["adj_close"], fsh, exr,
              np.array([s.delisted for s in stocks]), last_idx, first_idx,
              np.array([s.source for s in stocks]))
    return p.finalize()


# ------------------------------------------------------------------------------------------------ loaders
def _ms_to_date(ms):
    return (pd.to_datetime(ms, unit="ms") + pd.Timedelta(hours=8)).dt.strftime("%Y-%m-%d")


def want_code(code: str, ex: str) -> bool:
    if ex == "SH":
        return code.startswith(("60", "68"))
    if ex == "SZ":
        return code.startswith(("00", "30"))
    return False


def load_float_records(code: str, data_dir: Path = DATA_DIR) -> pd.Series:
    p = Path(data_dir) / "gbjg" / f"{code}.csv"
    if not p.exists():
        return pd.Series(dtype=float)
    g = pd.read_csv(p)
    if g.empty:
        return pd.Series(dtype=float)
    g = g.rename(columns={"变更日期": "date", "已上市流通A股": "float_a"})
    g["date"] = g["date"].astype(str).str[:10]
    g = g[g["date"] <= guard.current_limit()]
    g = g.sort_values("date").drop_duplicates("date", keep="last")
    s = pd.Series(pd.to_numeric(g["float_a"], errors="coerce").values, index=g["date"].values)
    # blank/zero float value = unknown: use the last known value (declared)
    s = s.where(s > 0).ffill()
    return s


def load_dev_stocks(data_dir: Path = DATA_DIR, limit: str | None = None, diagnostics: dict | None = None,
                    only: set | None = None) -> list:
    """Reads local raw files, clips every file to the guard limit, builds StockBars (listed: THS early+dump; delisted: Tencent)."""
    limit = limit or guard.current_limit()
    guard.assert_date_allowed(limit)
    data_dir = Path(data_dir)
    diag = diagnostics if diagnostics is not None else {}
    tick = pd.read_csv(data_dir / "lists" / "ths_tickers.csv", dtype=str)
    tick = tick[np.array([want_code(c, e) for c, e in zip(tick["ticker"], tick["exchange"])], dtype=bool)]
    listed = {r.thscode: (r.ticker, r.exchange) for r in tick.itertuples() if only is None or r.thscode in only}
    # THS dump (2016-10-18 ..), clipped to limit at the file boundary
    k = pd.read_csv(data_dir / "dumps" / "daily_k.csv.gz", dtype={"thscode": str})
    k = k[k["thscode"].isin(listed)].copy()
    k["date"] = _ms_to_date(k["date_ms"])
    k = k[k["date"] <= limit]
    k = k.rename(columns={"open_price": "open", "high_price": "high", "low_price": "low", "close_price": "close",
                          "turnover": "amount"})[["thscode", "date", "open", "high", "low", "close", "volume", "amount"]]
    dump_by = {c: g.set_index("date").drop(columns="thscode") for c, g in k.groupby("thscode")}
    ev = pd.read_csv(data_dir / "dumps" / "adjustment_factors.csv.gz", dtype={"thscode": str})
    ev["ex"] = _ms_to_date(ev["ex_date_ms"])
    ev = ev[ev["ex"] <= limit]
    ev_by = {c: g for c, g in ev.groupby("thscode")}
    stocks = []
    n_skipped = 0
    cols = ["open", "high", "low", "close", "volume", "amount"]
    for thscode, (code, exch) in listed.items():
        parts = []
        pe = data_dir / "ths_early" / f"{thscode}.csv"
        if pe.exists():
            e = pd.read_csv(pe, dtype={"date": str})
            e = e[(e["date"] <= limit) & (e["date"] >= DATA_START)]
            if thscode in dump_by:
                e = e[e["date"] < dump_by[thscode].index.min()]    # dump supersedes the early file on overlap
            parts.append(e.set_index("date")[cols])
        if thscode in dump_by:
            parts.append(dump_by[thscode][cols])
        parts = [p for p in parts if len(p)]
        if not parts:
            continue
        df = pd.concat(parts).sort_index()
        df = df[~df.index.duplicated(keep="last")]
        df = df[(df["close"] > 0) & (df["open"] > 0)]
        if len(df) < 2:
            continue
        evs = ev_by.get(thscode)
        if evs is None:
            F, exr = np.ones(len(df)), np.ones(len(df))
        else:
            F, exr, sk = event_multipliers(df.index.values.astype(str), df["close"].to_numpy(float), evs)
            n_skipped += sk
        df["adj_open"] = df["open"].to_numpy() * F
        df["adj_close"] = df["close"].to_numpy() * F
        er = pd.Series(exr, index=df.index)
        er = er[(er - 1).abs() > 1e-12]
        stocks.append(StockBars(code, exch, df, er, load_float_records(code, data_dir), False, "ths"))
    diag["listed_stocks_loaded"] = len(stocks)
    diag["adjustment_events_skipped"] = n_skipped
    # delisted: Tencent hfq (adjusted) + raw
    dl = pd.read_csv(data_dir / "lists" / "delist_sh_sz.csv", dtype=str).drop_duplicates("code")
    dl = dl[np.array([want_code(c, e) for c, e in zip(dl["code"], dl["ex"])], dtype=bool)]
    dl = dl[~dl["code"].isin(set(tick["ticker"]))]
    if only is not None:
        dl = dl[dl["code"].isin(only)]
    n_dl = 0
    for r in dl.itertuples():
        ph, pr = data_dir / "tx" / f"{r.code}_hfq.csv", data_dir / "tx" / f"{r.code}_raw.csv"
        if not (ph.exists() and pr.exists()):
            continue
        h = pd.read_csv(ph, dtype={"date": str})
        raw = pd.read_csv(pr, dtype={"date": str})
        ended_in_window = bool(len(raw)) and str(raw['date'].iloc[-1])[:10] < limit   # file-boundary test
        h = h[(h["date"] <= limit) & (h["date"] >= DATA_START)].set_index("date")
        raw = raw[(raw["date"] <= limit) & (raw["date"] >= DATA_START)].set_index("date")
        if len(raw) < 2:
            continue
        common = raw.index.intersection(h.index)
        raw, h = raw.loc[common], h.loc[common]
        raw = raw[(raw["close"] > 0) & (raw["open"] > 0) & (h.loc[raw.index, "close"] > 0)]
        h = h.loc[raw.index]
        adj_c, adj_o, n_act, n_chg = tx_total_return_prices(raw["close"].to_numpy(), h["close"].to_numpy(),
                                                              raw["open"].to_numpy())
        df = pd.DataFrame({"open": raw["open"], "high": raw["high"], "low": raw["low"], "close": raw["close"],
                           "volume": raw["volume"], "amount": raw["amount"], "adj_open": adj_o, "adj_close": adj_c},
                          index=raw.index)
        diag["tx_action_days"] = diag.get("tx_action_days", 0) + n_act
        diag["tx_S_changes"] = diag.get("tx_S_changes", 0) + n_chg
        fct = adj_c / raw["close"].to_numpy()
        q = np.ones(len(df))
        q[1:] = fct[:-1] / fct[1:]
        er = pd.Series(q, index=df.index)
        er = er[(er - 1).abs() > 1e-9]
        stocks.append(StockBars(r.code, r.ex, df, er, load_float_records(r.code, data_dir), ended_in_window, "tx"))
        n_dl += 1
    diag["delisted_stocks_loaded"] = n_dl
    return stocks


def build_dev_panel(data_dir: Path = DATA_DIR, min_bars: int = 250, diagnostics: dict | None = None) -> Panel:
    """Real-data panel. Stocks with fewer than `min_bars` bars in the window can never be eligible and are dropped."""
    from .protocol import load_verified
    load_verified()
    stocks = load_dev_stocks(data_dir, diagnostics=diagnostics)
    stocks = [s for s in stocks if len(s.df) >= min_bars]
    p = assemble(stocks)
    guard.assert_dates_allowed(p.sessions)
    return p
