"""yfinance retry + Alpaca (read-only market data) fallback. No network: yf.download and the HTTP client are faked."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from urllib.parse import urlparse

import numpy as np
import pandas as pd
import pytest
import yfinance as yf

from dashboard import config, fetch
from dashboard import iex_fallback as alpaca
from dashboard.cli import run
from dashboard.safety import sanitize, setup_logging

AKEY, ASECRET = "PKDUMMYALPACAKEY12345", "dummyAlpacaSecret0987654321zzzz"
NOW = datetime(2026, 10, 19, 22, tzinfo=timezone.utc)
UNIVERSE = ("SPY", "QQQ", "IWM", "EFA", "EEM", "GLD", "TLT", "IEF")


@pytest.fixture(autouse=True)
def _keys(monkeypatch):
    monkeypatch.setenv("ALPACA_API_KEY", AKEY)
    monkeypatch.setenv("ALPACA_SECRET_KEY", ASECRET)


def synth(n=160, end="2026-10-16", seed=1, adj_scale=1.0):
    dates = pd.bdate_range(end=end, periods=n)
    rng = np.random.default_rng(seed)
    close = 100 * np.cumprod(1 + rng.normal(0, 0.01, n))
    openp = close * (1 + rng.normal(0, 0.002, n))
    adj = close * adj_scale
    return pd.DataFrame({"Date": dates.strftime("%Y-%m-%d"), "open": openp, "high": close * 1.01, "low": close * 0.99,
                         "close": close, "adj_close": adj, "adj_open": openp * adj / close,
                         "volume": 1e6, "dividends": 0.0})


def yf_frame(df):
    out = pd.DataFrame({"Open": df.open, "High": df.high, "Low": df.low, "Close": df.close, "Adj Close": df.adj_close,
                        "Volume": df.volume, "Dividends": 0.0})
    out.index = pd.DatetimeIndex(df.Date)
    return out


class FakeYF:
    def __init__(self, monkeypatch, empty=(), raises=()):
        self.calls = []
        self.empty, self.raises = set(empty), set(raises)
        self.frames = {s: synth(seed=i) for i, s in enumerate(UNIVERSE)}
        monkeypatch.setattr(yf, "download", self)

    def __call__(self, sym, *a, **k):
        self.calls.append(sym)
        if sym in self.raises:
            raise RuntimeError(f"boom {sym}")
        if sym in self.empty:
            return pd.DataFrame()
        if sym in ("^IRX", "SGOV"):
            return pd.DataFrame({"Close": [4.1, 4.2, 100.5]}, index=pd.bdate_range("2026-10-12", periods=3))
        return yf_frame(self.frames[sym])


class FakeResp:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self.payload


class FakeHttp:
    """Serves bars per symbol; `page` rows per response with next_page_token pagination. Records every URL."""
    def __init__(self, data, page=50, error=None):
        self.data, self.page, self.error, self.urls = data, page, error, []

    def get(self, url, params=None, headers=None, timeout=None):
        self.urls.append(url)
        assert headers["APCA-API-KEY-ID"] == AKEY and headers["APCA-API-SECRET-KEY"] == ASECRET
        assert not any(AKEY in str(v) or ASECRET in str(v) for v in (params or {}).values())
        if self.error:
            raise RuntimeError(self.error.format(k=AKEY, s=ASECRET))
        sym = urlparse(url).path.split("/")[3]
        df = self.data[sym][params["adjustment"] == "all"]
        df = df[df.Date >= params["start"]]
        off = int(params.get("page_token") or 0)
        chunk = df.iloc[off:off + self.page]
        col = "adj_close" if params["adjustment"] == "all" else "close"
        bars = [{"t": f"{r.Date}T04:00:00Z", "o": r.open, "h": r.high, "l": r.low, "c": getattr(r, col), "v": r.volume}
                for r in chunk.itertuples()]
        nxt = str(off + self.page) if off + self.page < len(df) else None
        return FakeResp({"bars": bars, "symbol": sym, "next_page_token": nxt})


def alpaca_data(df, scale=1.0):
    """data[sym][is_all]: index False -> raw bars, True -> adjustment=all bars (adj_close column holds the adjusted c)."""
    a = df.copy()
    a["adj_close"] = df.adj_close * scale
    return {False: df, True: a}


def write_cache(root, date_str, frames, sources=None):
    bars = {s: f.copy() for s, f in frames.items()}
    fr = fetch.FetchResult("US", bars, pd.Series([4.0, 4.1], index=["2026-10-01", "2026-10-02"]),
                           sources or {s: "yfinance" for s in bars}, "yfinance ^IRX(2021起)+研究数据(至2020)",
                           fetched_at_utc=NOW.isoformat())
    return fetch.write_snapshot(root, date_str, fr)


@pytest.fixture
def sleeps(monkeypatch):
    rec = []
    monkeypatch.setattr(fetch.time, "sleep", lambda s: rec.append(s))
    return rec


# ------------------------------------------------------------------------------------------------ tests
def test_sanitize_redacts_alpaca_and_fuyao_keys():
    s = sanitize(f"x {AKEY} y {ASECRET} z")
    assert AKEY not in s and ASECRET not in s and s.count("***") == 2


def test_empty_yfinance_retried_exactly_three_times_then_alpaca(monkeypatch, tmp_path, sleeps):
    fy = FakeYF(monkeypatch, empty=["EFA"])
    cache = {s: f.iloc[:-6] for s, f in fy.frames.items()}          # snapshot is 6 sessions older than "now"
    write_cache(tmp_path, "2026-10-08", cache)
    http = FakeHttp({"EFA": alpaca_data(fy.frames["EFA"], scale=1.01), "SGOV": alpaca_data(synth())}, page=2)
    fr = fetch.fetch_us(config.us_spec(), NOW, data_root=tmp_path, http=http)
    assert fy.calls.count("EFA") == 3                                 # empty counts as failure: 3 attempts, no more
    assert all(fy.calls.count(s) == 1 for s in UNIVERSE if s != "EFA")
    assert fr.sources["EFA"].startswith("Alpaca IEX(拼接于快照 2026-10-08") and fr.sources["SPY"] == "yfinance"
    assert fr.fallback_used is True
    assert fr.detail["EFA"]["yfinance_attempts"] == 3 and fr.detail["EFA"]["new_bars"] == 6
    assert any(n.startswith("EFA yfinance 连续 3 次失败") for n in fr.notes)
    assert 2.0 in sleeps and 4.0 in sleeps                            # backoff 2s, 4s between the three attempts


def test_no_fallback_when_yfinance_ok(monkeypatch, tmp_path, sleeps):
    FakeYF(monkeypatch)
    http = FakeHttp({})
    fr = fetch.fetch_us(config.us_spec(), NOW, data_root=tmp_path, http=http)
    assert fr.fallback_used is False and http.urls == []
    assert set(fr.sources.values()) == {"yfinance"}


def test_exception_also_retried_and_cash_proxy_and_irx_fall_back(monkeypatch, tmp_path, sleeps):
    fy = FakeYF(monkeypatch, raises=["QQQ"], empty=["SGOV", "^IRX"])
    write_cache(tmp_path, "2026-10-08", {s: f.iloc[:-6] for s, f in fy.frames.items()})
    sgov = synth()
    http = FakeHttp({"QQQ": alpaca_data(fy.frames["QQQ"]), "SGOV": alpaca_data(sgov)})
    fr = fetch.fetch_us(config.us_spec(), NOW, data_root=tmp_path, http=http)
    assert fy.calls.count("QQQ") == 3 and fy.calls.count("SGOV") == 3 and fy.calls.count("^IRX") == 3
    assert fr.sources["QQQ"].startswith("Alpaca IEX")
    assert fr.extra_prices["SGOV"] == pytest.approx(sgov.close.iloc[-1])
    assert "快照 2026-10-08" in fr.rate_source and fr.fallback_used


def test_fetch_error_when_no_cached_snapshot(monkeypatch, tmp_path, sleeps):
    fy = FakeYF(monkeypatch, empty=["EFA"])
    http = FakeHttp({"EFA": alpaca_data(fy.frames["EFA"])})
    with pytest.raises(fetch.FetchError, match="EFA"):
        fetch.fetch_us(config.us_spec(), NOW, data_root=tmp_path, http=http)


def test_cached_snapshot_must_be_yfinance_sourced_and_hash_ok(monkeypatch, tmp_path):
    f = {"EFA": synth()}
    write_cache(tmp_path, "2026-10-09", f, sources={"EFA": "Alpaca IEX(拼接于快照 2026-10-01)"})
    d = write_cache(tmp_path, "2026-10-08", f)
    assert fetch.load_cached_us_bars(tmp_path, "EFA")[0] == "2026-10-08"      # newer one is Alpaca-sourced: skipped
    (d / "US" / "EFA.csv").write_text("Date\n")                                # tampered: skipped
    assert fetch.load_cached_us_bars(tmp_path, "EFA") is None


def test_alpaca_pagination_and_bar_format():
    base = synth(n=25)
    data = alpaca_data(base, scale=0.97)
    http = FakeHttp({"SPY": data}, page=10)
    out = alpaca.fetch_alpaca_bars("SPY", start="2000-01-01", http=http)
    assert len(http.urls) == 6                                                # 3 pages for raw + 3 for adjustment=all
    assert list(out.columns) == fetch.BAR_COLUMNS and len(out) == 25
    assert out.Date.tolist() == base.Date.tolist()
    np.testing.assert_allclose(out.close, base.close)
    np.testing.assert_allclose(out.adj_close, base.close * 0.97)
    np.testing.assert_allclose(out.adj_open, base.open * out.adj_close / base.close)


def test_splice_scales_junction_and_preserves_returns():
    full = synth(n=160, seed=7)
    cached = full.iloc[:-5].copy()                                            # yfinance history level
    # Alpaca adj level differs by 1.37 (e.g. later dividend) and has a 0.3% IEX print difference at the junction
    alp = full.copy()
    alp["adj_close"] = alp.adj_close * 1.37
    alp["adj_open"] = alp.open * alp.adj_close / alp.close
    out, j, factor, n_new = fetch.splice_bars(cached, alp, "X")
    assert j == cached.Date.iloc[-1] and n_new == 5 and len(out) == 160
    assert factor == pytest.approx(1 / 1.37)
    pd.testing.assert_frame_equal(out.iloc[:-5].reset_index(drop=True), cached.reset_index(drop=True))
    r_out = out.adj_close.pct_change().iloc[-5:].to_numpy()
    r_alp = alp.adj_close.pct_change().iloc[-5:].to_numpy()
    np.testing.assert_allclose(r_out, r_alp, rtol=1e-12)                      # returns preserved across the junction
    assert out.adj_close.iloc[-6] == cached.adj_close.iloc[-1]                # level continuous
    np.testing.assert_allclose(out.close.iloc[-5:], alp.close.iloc[-5:])      # raw stays Alpaca raw
    np.testing.assert_allclose(out.adj_open.iloc[-5:], out.open.iloc[-5:] * out.adj_close.iloc[-5:] / out.close.iloc[-5:])


def test_splice_requires_junction_in_both():
    full = synth(n=120)
    with pytest.raises(fetch.FetchError, match="junction"):
        fetch.splice_bars(full.iloc[:-3], full.iloc[:-3].iloc[:-1].assign(Date=lambda d: d.Date), "X")
    with pytest.raises(fetch.FetchError, match="implausible"):
        fetch.splice_bars(full.iloc[:-3], full.assign(adj_close=full.adj_close * 5), "X")


def test_keys_never_in_logs_or_html_even_if_http_error_contains_them(monkeypatch, tmp_path, sleeps, caplog):
    fy = FakeYF(monkeypatch, empty=["EFA"])
    write_cache(tmp_path / "d", "2026-10-08", {s: f.iloc[:-6] for s, f in fy.frames.items()})
    http = FakeHttp({}, error="401 for key {k} secret {s} url ?APCA-API-KEY-ID={k}")
    log_path = tmp_path / "dash.log"
    lg = setup_logging(log_path)
    lg.addHandler(caplog.handler)
    caplog.set_level(logging.DEBUG, logger="dashboard")
    from research_v2.calendar import get_trading_calendar
    markets = {"US": (config.us_spec, lambda spec, now: fetch.fetch_us(spec, now, data_root=tmp_path / "d", http=http),
                      get_trading_calendar)}
    rc = run(now_utc=NOW, output_dir=tmp_path / "o", data_root=tmp_path / "d", markets=markets, freeze_date="2026-10-19")
    assert rc == 1                                                            # Alpaca failed too -> US section error
    html = (tmp_path / "o" / "index.html").read_text()
    blobs = [html, log_path.read_text(), caplog.text, "\n".join(r.getMessage() for r in caplog.records)]
    assert "EFA" in html and "Alpaca" in html
    for b in blobs:
        assert AKEY not in b and ASECRET not in b
    assert len(http.urls) >= 1


def test_only_market_data_endpoints_requested(monkeypatch, tmp_path, sleeps):
    fy = FakeYF(monkeypatch, empty=["EFA", "SGOV"])
    write_cache(tmp_path, "2026-10-08", {s: f.iloc[:-6] for s, f in fy.frames.items()})
    http = FakeHttp({"EFA": alpaca_data(fy.frames["EFA"]), "SGOV": alpaca_data(synth())})
    fetch.fetch_us(config.us_spec(), NOW, data_root=tmp_path, http=http)
    assert http.urls
    for u in http.urls:
        p = urlparse(u)
        assert p.netloc == "data.alpaca.markets" and p.path.startswith("/v2/stocks/") and p.path.endswith("/bars")
        assert not any(w in u for w in ("paper-api", "api.alpaca.markets", "order", "account", "positions"))
    for bad in ("https://paper-api.alpaca.markets/v2/orders", "https://api.alpaca.markets/v2/account",
                "https://data.alpaca.markets/v2/orders", "http://data.alpaca.markets/v2/stocks/SPY/bars"):
        with pytest.raises(alpaca.AlpacaError, match="refused"):
            alpaca._get(bad, {}, http=http)


def test_old_manifest_without_source_detail_still_loads(tmp_path):
    d = write_cache(tmp_path, "2026-10-08", {"SPY": synth()})
    import json
    mp = d / "manifest.json"
    m = json.loads(mp.read_text())
    m["markets"]["US"].pop("source_detail")
    mp.write_text(json.dumps(m))
    fr = fetch.load_snapshot(d, "US")
    assert fr.detail == {} and fr.sources == {"SPY": "yfinance"}
