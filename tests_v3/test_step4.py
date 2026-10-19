from __future__ import annotations

import math
from statistics import NormalDist

import numpy as np
import pandas as pd
import pytest

from research_v2.signals import compute_matched_monthly_signals, compute_model_signals_for_events
from research_v3.cash_yield import CashYieldCurve, load_irx
from research_v3.config import V3_DEFAULT
from research_v3.engine import run_backtest_v3
from research_v3.protocol_step4 import HASH_PATH, PROTOCOL_PATH, ProtocolChangedError, load_verified
from research_v3.stats import (bootstrap_sharpe_diff, dsr, expected_max_sr, psr, psr_from_moments,
                               sample_moments, stationary_bootstrap_indices)
from research_v3.step2 import _setup
from research_v3.step4 import benchmark_frames, spy_sma_decision

N = NormalDist()


# ---------- protocol ----------
def test_protocol_verifies_and_rejects_modification(tmp_path):
    assert load_verified()["trial_count"]["expected_n_trials"] == 78
    p = tmp_path / "p.json"
    p.write_text(PROTOCOL_PATH.read_text().replace('"seed": 20261019', '"seed": 1'))
    with pytest.raises(ProtocolChangedError):
        load_verified(p, HASH_PATH)


# ---------- stats ----------
def test_psr_hand_computed():
    # SR=0.1, T=101, normal moments: denom = sqrt(1 + 0.5*0.01) = sqrt(1.005); z = 0.1*10/sqrt(1.005) = 0.99751
    z = 1.0 / math.sqrt(1.005)
    assert psr_from_moments(0.1, 101, 0.0, 3.0, 0.0) == pytest.approx(N.cdf(z), abs=1e-12)
    assert psr_from_moments(0.1, 101, 0.0, 3.0, 0.0) == pytest.approx(0.84074, abs=2e-5)  # hand: Phi(1)-0.00249*0.24197
    # skew and kurtosis enter: denom = 1 - (-0.5)(0.1) + (6-1)/4*0.01 = 1.0625
    z2 = 1.0 / math.sqrt(1.0625)
    assert psr_from_moments(0.1, 101, -0.5, 6.0, 0.0) == pytest.approx(N.cdf(z2), abs=1e-12)
    # SR == SR* -> 0.5
    assert psr_from_moments(0.1, 101, 0.0, 3.0, 0.1) == pytest.approx(0.5)


def test_expected_max_sr_hand_computed():
    # N=10, V=0.04: 0.2 * (0.4227843*1.281552 + 0.5772157*Phi^-1(1-1/(10e)))
    g = 0.5772156649015329
    v = 0.2 * ((1 - g) * 1.2815515655 + g * N.inv_cdf(1 - 1 / (10 * math.e)))
    assert expected_max_sr(0.04, 10) == pytest.approx(v, abs=1e-9)
    assert expected_max_sr(0.04, 10) == pytest.approx(0.315, abs=2e-3)  # hand value
    assert expected_max_sr(0.04, 1) == 0.0
    assert expected_max_sr(0.04, 100) > expected_max_sr(0.04, 10) > 0


def test_psr_dsr_on_series_and_ordering():
    rng = np.random.default_rng(0)
    x = rng.normal(0.0008, 0.01, 1000)
    sr, g3, g4, t = sample_moments(x)
    assert t == 1000 and sr == pytest.approx(x.mean() / x.std(ddof=1))
    assert g3 == pytest.approx(float(np.mean((x - x.mean()) ** 3) / np.var(x) ** 1.5))
    assert 2.5 < g4 < 3.6
    assert psr(x) == pytest.approx(psr_from_moments(sr, t, g3, g4, 0.0))
    v = 0.0003 / 252
    assert dsr(x, 78, v) == pytest.approx(psr(x, expected_max_sr(v, 78)))
    assert dsr(x, 78, v) < psr(x)
    assert dsr(x, 78, 4 * v) < dsr(x, 78, v)  # more dispersion -> bigger hurdle
    assert dsr(x, 200, v) < dsr(x, 20, v)      # more trials -> bigger hurdle


# ---------- bootstrap ----------
def test_bootstrap_deterministic_and_seed_sensitive():
    a = stationary_bootstrap_indices(300, 21, 50, seed=7)
    b = stationary_bootstrap_indices(300, 21, 50, seed=7)
    c = stationary_bootstrap_indices(300, 21, 50, seed=8)
    assert a.shape == (50, 300) and (a == b).all() and not (a == c).all()
    assert a.min() >= 0 and a.max() < 300
    # mean block length ~ 21: fraction of non-consecutive steps ~ 1/21
    cont = ((a[:, 1:] - a[:, :-1]) % 300 == 1).mean()
    assert 0.93 < cont < 0.99
    rng = np.random.default_rng(1)
    x, y = rng.normal(0.0005, 0.01, 300), rng.normal(0.0003, 0.01, 300)
    r1 = bootstrap_sharpe_diff(x, y, a)
    r2 = bootstrap_sharpe_diff(x, y, b)
    assert r1 == r2 and r1[1] < r1[2]


# ---------- SPY_SMA10 look-ahead ----------
def _month_series():
    dates, closes = [], []
    for i, d in enumerate(pd.bdate_range("2019-01-01", "2020-03-31")):
        dates.append(d.strftime("%Y-%m-%d"))
        closes.append(100.0 + i * 0.1)
    return dates, closes


def test_sma10_no_lookahead_and_logic():
    dates, closes = _month_series()
    sig = "2019-12-31"
    base = spy_sma_decision(dates, closes, sig)
    assert base == "SPY"  # monotone rising
    # change everything after the signal date: decision unchanged
    c2 = [c if d <= sig else 1e6 * c for d, c in zip(dates, closes)]
    assert spy_sma_decision(dates, c2, sig) == base
    c3 = [c if d <= sig else 0.0001 for d, c in zip(dates, closes)]
    assert spy_sma_decision(dates, c3, sig) == base
    # a crash on the signal day itself flips it to CASH
    i = dates.index(sig)
    c4 = list(closes)
    c4[i] = 1.0
    assert spy_sma_decision(dates, c4, sig) == "CASH"
    # exactly 10 values needed: signal at the end of the 9th month of data -> CASH
    assert spy_sma_decision(dates, closes, "2019-09-30") == "CASH"
    assert spy_sma_decision(dates, closes, "2019-10-31") == "SPY"
    # mid-month signal uses only prior month-ends plus signal close
    mid = "2019-12-18"
    me = [closes[max(j for j, d in enumerate(dates) if d[:7] == m)] for m in
          ["2019-03", "2019-04", "2019-05", "2019-06", "2019-07", "2019-08", "2019-09", "2019-10", "2019-11"]]
    sma = np.mean(me + [closes[dates.index(mid)]])
    assert (closes[dates.index(mid)] > sma) == (spy_sma_decision(dates, closes, mid) == "SPY")
    # dates not in data -> CASH (cannot trade on missing signal close)
    assert spy_sma_decision(dates, closes, "2019-12-29") == "CASH"


# ---------- benchmark plumbing on real train data ----------
@pytest.fixture(scope="module")
def train_env():
    view, sessions, weekly, monthly = _setup("train")
    wf = compute_model_signals_for_events(bars=view.bars, session_dates=sessions, weekly_events=weekly,
                                          model_id="W_M12_T40", momentum_months=12, trend_weeks=40)
    mrows = compute_matched_monthly_signals(wf, monthly, "M_M12_T40").rows
    return view, mrows, CashYieldCurve(load_irx()), benchmark_frames(view, mrows)


def test_spy_bh_nav_tracks_spy_after_single_buy(train_env):
    view, mrows, curve, frames = train_env
    f = frames["SPY_BH"]
    assert len(f.rows) == 1 and f.rows[0]["weights"]["SPY"] == 1.0
    nav, reb, m = run_backtest_v3(view, f, V3_DEFAULT, curve)
    assert len(reb) == 1
    ex = f.rows[0]["execution_session"]
    px = dict(zip(view.bars["SPY"]["Date"].astype(str), view.bars["SPY"]["adj_close"].astype(float)))
    after = nav[nav.date > ex]
    prev = nav[nav.date >= ex].nav.to_numpy()[:-1]
    ratio = after.nav.to_numpy() / prev
    exp = np.array([px[d] for d in after.date]) / np.array([px[d] for d in nav[nav.date >= ex].date.iloc[:-1]])
    assert np.allclose(ratio, exp, rtol=0, atol=1e-9)           # NAV path == SPY path scaled
    assert (nav[nav.date > ex].cash.abs() < 1e-6 * nav[nav.date > ex].nav).all()  # no cash held after the buy
    fee = float(reb.iloc[0]["fee"])
    assert fee == pytest.approx(0.0010 * reb.iloc[0]["gross_traded_notional"], rel=1e-6)  # 10 bps one-time cost
    assert nav[nav.date > ex].cash_interest.abs().max() < 1e-3  # no meaningful cash interest after the buy


def test_6040_and_ew8_weights_and_rebalance_count(train_env):
    view, mrows, curve, frames = train_env
    f = frames["60_40"]
    assert all(r["weights"]["SPY"] == 0.6 and r["weights"]["IEF"] == 0.4 and r["weights"]["CASH"] == 0.0 for r in f.rows)
    nav, reb, _ = run_backtest_v3(view, f, V3_DEFAULT, curve)
    assert len(reb) == len(f.rows) > 100
    e = frames["EW8"]
    assert all(abs(sum(v for k, v in r["weights"].items() if k != "CASH") - 1.0) < 1e-12 for r in e.rows)
    assert {r["execution_session"] for r in e.rows} == {r["execution_session"] for r in f.rows}


def test_sma10_frame_uses_only_signal_date_data(train_env):
    view, mrows, curve, frames = train_env
    f = frames["SPY_SMA10"]
    spy = view.bars["SPY"]
    d, c = spy["Date"].astype(str).tolist(), spy["adj_close"].astype(float).tolist()
    for r in f.rows[:40]:
        dec = spy_sma_decision(d, c, r["signal_session"])
        # recompute with future data truncated at the signal date
        k = d.index(r["signal_session"]) + 1
        assert spy_sma_decision(d[:k], c[:k], r["signal_session"]) == dec
        assert r["weights"]["SPY" if dec == "SPY" else "CASH"] == 1.0
    assert all(r["execution_session"] > r["signal_session"] for r in f.rows)
