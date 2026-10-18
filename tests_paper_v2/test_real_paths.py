"""Tests exercising REAL research_v2 signal code, yfinance normalization, dividends, atomic save, as-of."""
import json
import os
import shutil

import numpy as np
import pandas as pd
import pytest
from unittest.mock import patch

from paper_v2 import config
from paper_v2.asof import normalize_as_of
from paper_v2.cli import main as cli_main
from paper_v2.config import MODELS, PAPER_V2_STATE_DIR, UNIVERSE
from paper_v2.journal import AppendOnlyJournal
from paper_v2.market_data import BAR_COLUMNS, normalize_yf_frame
from paper_v2.phases import credit_dividends, run_execution_phase, run_signal_phase
from paper_v2.state import PaperState
from research_v2.calendar import get_trading_calendar, parse_as_of
from research_v2.data import REQUIRED_COLUMNS


@pytest.fixture(autouse=True)
def clean_state():
    shutil.rmtree(PAPER_V2_STATE_DIR, ignore_errors=True)
    config.setup_dirs()
    yield
    shutil.rmtree(PAPER_V2_STATE_DIR, ignore_errors=True)


def synthetic_bars(start="2004-06-01", end="2020-12-31", dividends=None):
    """Deterministic bars in research_v2 snapshot format (Date str, adj_open, ...) + dividends col."""
    sessions = get_trading_calendar().sessions_in_range(start, end).strftime("%Y-%m-%d").tolist()
    bars = {}
    for k, sym in enumerate(UNIVERSE):
        rng = np.random.default_rng(100 + k)
        drift = 0.0004 - 0.0001 * (k % 3)
        close = 100 * np.exp(np.cumsum(drift + 0.01 * rng.standard_normal(len(sessions))))
        df = pd.DataFrame({
            "Date": sessions,
            "open": close * 0.999, "high": close * 1.01, "low": close * 0.98, "close": close,
            "adj_close": close * 0.9, "adj_open": close * 0.999 * 0.9,
            "volume": 1_000_000, "dividends": 0.0,
        })
        bars[sym] = df
    for (sym, date), amt in (dividends or {}).items():
        bars[sym].loc[bars[sym]["Date"] == date, "dividends"] = amt
    return bars


# ---------- real signal code, no mocking of compute_model_signals_for_events ----------

def test_signal_phase_real_research_code():
    bars = synthetic_bars()
    for df in bars.values():
        assert set(REQUIRED_COLUMNS) <= set(df.columns)
    with patch("paper_v2.phases.fetch_market_data", return_value=(bars, "synthetic_hash")):
        run_signal_phase("2020-12-31")

    state = PaperState()
    signals = AppendOnlyJournal(config.SIGNAL_JOURNAL_PATH).read_all()
    assert len(signals) == len(MODELS) == 18
    for ev in signals:
        assert abs(sum(ev["weights"].values()) + ev["cash_weight"] - 1.0) < 1e-9
        assert ev["strategy_commit"] != "HEAD"
        assert ev["config_hash"] != "CFG_HASH" and len(ev["config_hash"]) == 64
        assert ev["source_data_hash"] == "synthetic_hash"
    w = state.get_model_state("W_M06_T36")["pending_order"]
    assert w["signal_session"] == "2020-12-31"
    assert w["status"] == "PENDING"
    # idempotent rerun
    with patch("paper_v2.phases.fetch_market_data", return_value=(bars, "synthetic_hash")):
        run_signal_phase("2020-12-31")
    assert len(AppendOnlyJournal(config.SIGNAL_JOURNAL_PATH).read_all()) == 18


def test_cli_signal_offline_bars_dir(tmp_path, monkeypatch, capsys):
    bars = synthetic_bars()
    for sym, df in bars.items():
        df.drop(columns=["dividends"]).to_csv(tmp_path / f"{sym}.csv", index=False)
    monkeypatch.delenv("PAPER_V2_OFFLINE_BARS_DIR", raising=False)
    rc = cli_main(["signal", "--as-of", "2020-12-31", "--bars-dir", str(tmp_path)])
    monkeypatch.delenv("PAPER_V2_OFFLINE_BARS_DIR", raising=False)
    assert rc == 0
    assert "Signal phase complete" in capsys.readouterr().out
    assert len(AppendOnlyJournal(config.SIGNAL_JOURNAL_PATH).read_all()) == 18
    # bars persisted (not just hashes)
    persisted = [d for d in os.listdir(config.DATA_SNAPSHOTS_DIR) if d.startswith("bars_")]
    assert len(persisted) == 1
    assert sorted(os.listdir(os.path.join(config.DATA_SNAPSHOTS_DIR, persisted[0]))) == sorted(f"{s}.csv" for s in UNIVERSE)


def test_cli_mark_verify_not_implemented(capsys):
    assert cli_main(["mark"]) != 0
    assert cli_main(["verify"]) != 0
    assert "not implemented" in capsys.readouterr().err


# ---------- yfinance normalization ----------

def fake_yf_frame(multiindex=True, tz=True):
    idx = pd.DatetimeIndex(["2024-03-14", "2024-03-15", "2024-03-18"])
    if tz:
        idx = idx.tz_localize("America/New_York")
    idx.name = "Date"
    data = {
        "Open": [100.0, 101.0, 102.0], "High": [103.0, 103.0, 104.0], "Low": [99.0, 100.0, 101.0],
        "Close": [102.0, 100.0, 103.0], "Adj Close": [51.0, 50.0, 51.5],
        "Volume": [10, 20, 30], "Dividends": [0.0, 1.5, 0.0], "Stock Splits": [0.0, 0.0, 0.0],
    }
    df = pd.DataFrame(data, index=idx)
    if multiindex:
        df.columns = pd.MultiIndex.from_product([df.columns, ["SPY"]], names=["Price", "Ticker"])
    return df


@pytest.mark.parametrize("multiindex", [True, False])
@pytest.mark.parametrize("tz", [True, False])
def test_normalize_yf_frame(multiindex, tz):
    out = normalize_yf_frame(fake_yf_frame(multiindex, tz))
    assert list(out.columns) == BAR_COLUMNS
    assert set(REQUIRED_COLUMNS) <= set(out.columns)
    assert out["Date"].tolist() == ["2024-03-14", "2024-03-15", "2024-03-18"]
    np.testing.assert_allclose(out["adj_open"], [100 * 51 / 102, 101 * 50 / 100, 102 * 51.5 / 103])
    assert out["dividends"].tolist() == [0.0, 1.5, 0.0]


def test_normalize_yf_frame_as_of_filter_and_missing_adj_close():
    out = normalize_yf_frame(fake_yf_frame(), "2024-03-15")
    assert out["Date"].tolist() == ["2024-03-14", "2024-03-15"]
    raw = fake_yf_frame(multiindex=False).drop(columns=["Adj Close"])
    with pytest.raises(ValueError):
        normalize_yf_frame(raw)


def test_fetch_uses_auto_adjust_false(tmp_path):
    from paper_v2 import market_data
    calls = []

    def fake_download(sym, **kw):
        calls.append(kw)
        return fake_yf_frame()

    with patch.object(market_data.yf, "download", side_effect=fake_download):
        bars, h = market_data.fetch_market_data("2024-03-18")
    assert all(kw["auto_adjust"] is False and kw["actions"] is True for kw in calls)
    assert os.path.isdir(os.path.join(config.DATA_SNAPSHOTS_DIR, f"bars_{h}"))
    assert set(bars) == set(UNIVERSE)


# ---------- dividends ----------

def test_credit_dividends_idempotent():
    bars = synthetic_bars("2020-01-01", "2020-03-31", {("SPY", "2020-02-14"): 1.5, ("TLT", "2020-02-14"): 0.5})
    ms = {"cash": 1000.0, "nav": 5000.0, "shares": {"SPY": 10.0}, "last_dividend_date": "2020-02-01"}
    got = credit_dividends(ms, bars, "2020-02-20")
    assert got == pytest.approx(15.0)  # TLT not held
    assert ms["cash"] == pytest.approx(1015.0) and ms["nav"] == pytest.approx(5015.0)
    assert ms["last_dividend_date"] == "2020-02-20"
    # rerun, and rerun with an earlier last_dividend_date: no double count
    assert credit_dividends(ms, bars, "2020-02-20") == 0.0
    ms["last_dividend_date"] = "2020-02-01"
    assert credit_dividends(ms, bars, "2020-03-31") == 0.0
    assert ms["cash"] == pytest.approx(1015.0)
    assert ms["dividends_credited"] == {"SPY|2020-02-14": pytest.approx(15.0)}


def test_execution_phase_credits_dividend_once():
    bars = synthetic_bars("2020-01-01", "2020-03-31", {("SPY", "2020-02-14"): 2.0})
    state = PaperState()
    ms = state.get_model_state("W_M06_T36")
    ms.update({"shares": {"SPY": 100.0}, "cash": 0.0, "nav": 10000.0, "last_dividend_date": "2020-02-03"})
    state.save()
    with patch("paper_v2.phases.fetch_market_data", return_value=(bars, "h")):
        run_execution_phase("2020-02-20")
        run_execution_phase("2020-02-20")
        run_execution_phase("2020-03-02")
    ms = PaperState().get_model_state("W_M06_T36")
    assert ms["cash"] == pytest.approx(200.0)
    assert ms["dividends_total"] == pytest.approx(200.0)
    divs = [e for e in AppendOnlyJournal(config.EXEC_JOURNAL_PATH).read_all() if e["status"] == "DIVIDEND_CREDITED"]
    assert len(divs) == 1 and divs[0]["amount"] == pytest.approx(200.0)


# ---------- atomic save ----------

def test_atomic_save(tmp_path):
    state = PaperState()
    state.update_model_state("W_M06_T36", {"cash": 123.0})
    state.save()
    path = state.state_path
    # a failing serialization must leave the old file intact (no truncation) and no temp files
    state.update_model_state("W_M06_T36", {"cash": 456.0})
    with patch("paper_v2.state.json.dumps", side_effect=RuntimeError("boom")):
        with pytest.raises(RuntimeError):
            state.save()
    assert json.load(open(path))["models"]["W_M06_T36"]["cash"] == 123.0
    # failure during the write/replace leaves old content and cleans temp
    with patch("paper_v2.fsutil.os.replace", side_effect=OSError("boom")):
        with pytest.raises(OSError):
            state.save()
    assert json.load(open(path))["models"]["W_M06_T36"]["cash"] == 123.0
    assert not [f for f in os.listdir(os.path.dirname(path)) if f.endswith(".tmp")]
    state.save()
    assert PaperState().get_model_state("W_M06_T36")["cash"] == 456.0


def test_save_uses_replace():
    state = PaperState()
    with patch("paper_v2.fsutil.os.replace", wraps=os.replace) as rep:
        state.save()
    assert rep.call_count == 1


# ---------- as-of parsing ----------

def test_normalize_as_of():
    d, ts = normalize_as_of("2020-12-31")
    assert d == "2020-12-31"
    assert parse_as_of(ts).isoformat().startswith("2020-12-31T21:01")  # 16:01 EST in UTC
    d, ts = normalize_as_of("2020-07-01T20:30:00+00:00")
    assert d == "2020-07-01"
    parse_as_of(ts)
    for bad in ("2020-12-31T16:00:00", "12/31/2020", "2020-13-01", "garbage"):
        with pytest.raises(ValueError):
            normalize_as_of(bad)
