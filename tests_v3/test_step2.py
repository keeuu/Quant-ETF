from __future__ import annotations

import json
import math

import numpy as np
import pandas as pd
import pytest

from research_v2.calendar import get_trading_calendar
from research_v2.contracts import DatasetManifest, DatasetView, SignalFrame, UNIVERSE

from research_v3.config import V3_DEFAULT
from research_v3.engine import run_backtest_v3
from research_v3.guards import HoldoutAccessError
from research_v3.protocol_step2 import HASH_PATH, PROTOCOL_PATH, ProtocolChangedError, load_verified
from research_v3.runner import prepare_signals
from research_v3.signals_v3 import combine_momentum, make_sigma_provider, reselect, tranche_rows
from research_v3.tranche import run_tranches
from research_v3.cash_yield import CashYieldCurve, load_irx


def _frame(rows, mid="T"):
    return SignalFrame(model_id=mid, rows=rows, lineage=[])


def _row(sig, mom, trend=None, ex="2010-01-05"):
    trend = trend if trend is not None else {s: True for s in UNIVERSE}
    return {"signal_session": sig, "execution_session": ex, "status": "COMPLETE",
            "momentum_scores": {s: mom.get(s) for s in UNIVERSE}, "trend_passes": {s: bool(trend.get(s, False)) for s in UNIVERSE}}


# ---------- protocol ----------
def test_protocol_verifies_and_rejects_modification(tmp_path):
    assert load_verified()["parameter_search"] == "none"
    p = tmp_path / "p.json"
    p.write_text(PROTOCOL_PATH.read_text().replace('"buffer_rank": 4', '"buffer_rank": 5'))
    with pytest.raises(ProtocolChangedError):
        load_verified(p, HASH_PATH)


# ---------- ENS ----------
@pytest.fixture(scope="module")
def train():
    return prepare_signals("train")


def test_ens_single_lookback_equals_base_and_reselect_identity(train):
    _view, frames = train
    base = frames["W_M12_T40"]
    ens = combine_momentum([base], "X")
    assert [r["weights"] for r in ens.rows] == [r["weights"] for r in base.rows]
    assert [r["weights"] for r in reselect(base).rows] == [r["weights"] for r in base.rows]


def test_ens_is_mean_of_lookbacks(train):
    _view, frames = train
    a, b = frames["W_M06_T40"], frames["W_M12_T40"]
    e = combine_momentum([a, b], "X")
    r = e.rows[5]
    ra = next(x for x in a.rows if x["signal_session"] == r["signal_session"])
    rb = next(x for x in b.rows if x["signal_session"] == r["signal_session"])
    for s in UNIVERSE:
        assert r["momentum_scores"][s] == pytest.approx((ra["momentum_scores"][s] + rb["momentum_scores"][s]) / 2)


# ---------- BUF ----------
def test_buffer_keeps_rank4_drops_rank5_and_fails_trend():
    # momentum order: SPY>QQQ>IWM>EFA>EEM>GLD...
    mom = {s: 1.0 - 0.1 * i for i, s in enumerate(UNIVERSE)}
    r0 = _row("2010-01-01", {**mom, "EFA": 0.0, "EEM": -0.1, "GLD": -0.2, "TLT": -0.3, "IEF": -0.4,
                              "SPY": 1.0, "QQQ": 0.9, "IWM": 0.8}, ex="2010-01-04")
    # first row: selected SPY,QQQ,IWM (no state). Second row: EFA is rank 4, IWM falls to rank 4 -> check keeps
    r1 = _row("2010-01-08", {"SPY": 1.0, "QQQ": 0.9, "EFA": 0.8, "EEM": 0.7, "IWM": 0.6, "GLD": 0.1}, ex="2010-01-11")
    # rank of IWM = 5 (SPY,QQQ,EFA,EEM,IWM) -> dropped; SPY,QQQ kept; filled by EFA
    out = reselect(_frame([r0, r1]), buffer_rank=4).rows
    assert set(out[0]["selected_symbols"]) == {"SPY", "QQQ", "IWM"}
    assert set(out[1]["selected_symbols"]) == {"SPY", "QQQ", "EFA"}
    # rank 4 kept: IWM at rank 4
    r2 = _row("2010-01-15", {"SPY": 1.0, "QQQ": 0.9, "EFA": 0.8, "IWM": 0.7, "EEM": 0.6}, ex="2010-01-19")
    out = reselect(_frame([r0, r2]), buffer_rank=4).rows
    assert "IWM" in out[1]["selected_symbols"] and set(out[1]["selected_symbols"]) == {"SPY", "QQQ", "IWM"}
    # without buffer EFA would replace IWM
    plain = reselect(_frame([r0, r2])).rows
    assert set(plain[1]["selected_symbols"]) == {"SPY", "QQQ", "EFA"}
    # failing trend dropped regardless of rank (IWM still rank 1 by momentum but trend fails)
    r3 = _row("2010-01-15", {"IWM": 5.0, "SPY": 1.0, "QQQ": 0.9, "EFA": 0.8}, trend={"SPY": 1, "QQQ": 1, "EFA": 1},
              ex="2010-01-19")
    out = reselect(_frame([r0, r3]), buffer_rank=4).rows
    assert "IWM" not in out[1]["selected_symbols"]
    assert out[1]["weights"]["IWM"] == 0.0


# ---------- IVOL ----------
def _bars(n=120, vol=None, shock_after=None):
    sess = [d.strftime("%Y-%m-%d") for d in pd.bdate_range("2010-01-04", periods=n)]
    rng = np.random.default_rng(0)
    bars = {}
    for sym in UNIVERSE:
        v = (vol or {}).get(sym, 0.01)
        px = 100 * np.exp(np.cumsum(rng.normal(0, v, n)))
        if shock_after is not None:
            px[shock_after:] *= 3.0
        bars[sym] = pd.DataFrame({"Date": sess, "adj_close": px, "adj_open": px})
    return bars, sess


def test_ivol_weights_sum_and_ordering():
    bars, sess = _bars(vol={"SPY": 0.005, "QQQ": 0.02, "IWM": 0.01})
    sig = make_sigma_provider(bars, 63)
    r = _row(sess[100], {"SPY": 1.0, "QQQ": 0.9, "IWM": 0.8})
    r["trend_passes"] = {s: s in ("SPY", "QQQ", "IWM") for s in UNIVERSE}
    w = reselect(_frame([r]), sigma=sig).rows[0]["weights"]
    ew = reselect(_frame([r])).rows[0]["weights"]
    assert sum(w[s] for s in UNIVERSE) == pytest.approx(sum(ew[s] for s in UNIVERSE))
    assert w["SPY"] > w["IWM"] > w["QQQ"] > 0
    assert w["EFA"] == 0 and w["CASH"] == 0
    # partial fill (2 assets) keeps risky sum = 1; none -> CASH
    r2 = _row(sess[100], {"SPY": 1.0, "QQQ": 0.9}, trend={"SPY": 1, "QQQ": 1})
    assert sum(reselect(_frame([r2]), sigma=sig).rows[0]["weights"][s] for s in UNIVERSE) == pytest.approx(1.0)
    r3 = _row(sess[100], {"SPY": 1.0}, trend={})
    assert reselect(_frame([r3]), sigma=sig).rows[0]["weights"]["CASH"] == 1.0


def test_ivol_no_lookahead():
    bars, sess = _bars()
    bars2, _ = _bars(shock_after=101)
    t = sess[100]
    s1, s2 = make_sigma_provider(bars, 63), make_sigma_provider(bars2, 63)
    for sym in UNIVERSE:
        assert s1(t, sym) == s2(t, sym) and s1(t, sym) is not None
    assert s1(sess[110], "SPY") != s2(sess[110], "SPY")


# ---------- TRANCHE ----------
def test_tranche_rows_selection():
    rows = [_row(d, {"SPY": 1.0}) for d in ["2010-01-08", "2010-01-15", "2010-01-22", "2010-01-29", "2010-02-05", "2010-02-12"]]
    got = [[r["signal_session"] for r in tranche_rows(rows, k)] for k in range(4)]
    assert got[0] == ["2010-01-08", "2010-02-05"]
    assert got[3] == ["2010-01-29", "2010-02-12"]  # Feb has 2 events -> last
    assert got[2][1] == "2010-02-12"


def test_tranche_nav_identity_and_single_equals_base(train):
    view, frames = train
    curve = CashYieldCurve(load_irx())
    base_m = frames["M_M12_T40"]
    nav1, _r, m1 = run_tranches(view, [base_m], V3_DEFAULT, curve)
    navb, _rb, mb = run_backtest_v3(view, base_m, V3_DEFAULT, curve)
    assert np.allclose(nav1["nav"], navb["nav"], rtol=1e-10)
    assert m1["cagr"] == pytest.approx(mb["cagr"])
    # four tranches: initial split and NAV sum identity
    w = frames["W_M12_T40"]
    subs = [_frame(tranche_rows(w.rows, k), f"k{k}") for k in range(4)]
    import dataclasses
    tot, _reb, _m = run_tranches(view, subs, V3_DEFAULT, curve)
    cfg4 = dataclasses.replace(V3_DEFAULT, initial_nav=V3_DEFAULT.initial_nav / 4)
    parts = [run_backtest_v3(view, s, cfg4, curve)[0] for s in subs]
    assert len(parts) == 4
    assert np.allclose(tot["nav"], sum(p["nav"] for p in parts))
    assert parts[0]["nav"].iloc[0] == pytest.approx(25000.0, rel=0.05)
    assert tot["nav"].iloc[0] == pytest.approx(100000.0, rel=0.05)


# ---------- holdout guard ----------
def test_holdout_guard_still_enforced(train):
    view, frames = train
    from dataclasses import replace
    with pytest.raises(HoldoutAccessError):
        run_backtest_v3(replace(view, partition="holdout"), frames["W_M12_T40"], V3_DEFAULT, CashYieldCurve(load_irx()))
    with pytest.raises(HoldoutAccessError):
        run_tranches(replace(view, partition="holdout"), [frames["W_M12_T40"]], V3_DEFAULT, CashYieldCurve(load_irx()))
    from research_v3.step2 import _setup
    with pytest.raises(HoldoutAccessError):
        _setup("holdout")
