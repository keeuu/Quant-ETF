from __future__ import annotations

import copy
import dataclasses

import numpy as np
import pandas as pd
import pytest

from research_v2.contracts import SignalFrame, UNIVERSE
from research_v3.cash_yield import CashYieldCurve, load_irx
from research_v3.config import V3_DEFAULT
from research_v3.engine import run_backtest_v3
from research_v3.guards import HoldoutAccessError
from research_v3.protocol_step3 import HASH_PATH, PROTOCOL_PATH, ProtocolChangedError, load_verified
from research_v3.runner import prepare_signals
from research_v3.signals_step3 import build_weights, cap_select, make_defensive_provider, reselect3
from research_v3.signals_v3 import combine_momentum

GROUPS = load_verified()["asset_class_groups"]


def _row(sig, mom, trend=None):
    trend = trend if trend is not None else {s: True for s in mom}
    return {"signal_session": sig, "execution_session": "2010-01-05", "status": "COMPLETE",
            "momentum_scores": {s: mom.get(s) for s in UNIVERSE},
            "trend_passes": {s: bool(trend.get(s, False)) for s in UNIVERSE}}


def _sessions(n=200):
    return [d.strftime("%Y-%m-%d") for d in pd.bdate_range("2009-01-05", periods=n)]


def _bars(sess, ief_total=0.10, tail_mult=1.0):
    """IEF rises geometrically so that its total return from session 0 to 120 is ief_total."""
    out = {}
    for sym in UNIVERSE:
        px = np.full(len(sess), 100.0)
        if sym == "IEF":
            px = 100.0 * (1 + ief_total) ** (np.arange(len(sess)) / 120.0)
            px[121:] *= tail_mult
        out[sym] = pd.DataFrame({"Date": sess, "adj_close": px, "adj_open": px})
    return out


def _curve(sess, pct):
    return CashYieldCurve(pd.Series(pct, index=sess, dtype=float))


# ---------- protocol ----------
def test_protocol_verifies_and_rejects_modification(tmp_path):
    assert load_verified()["parameter_search"] == "none"
    p = tmp_path / "p.json"
    p.write_text(PROTOCOL_PATH.read_text().replace('"ivol_window": 63', '"ivol_window": 60'))
    with pytest.raises(ProtocolChangedError):
        load_verified(p, HASH_PATH)


# ---------- CASHSLOT ----------
def test_cashslot_one_passing_asset():
    r = _row("2010-01-01", {"SPY": 0.5, "QQQ": 0.4}, trend={"SPY": 1})
    w, sel, dw = build_weights(r, cap_groups=None, slots="cashslot", sigma=None, defensive=None)
    assert sel == ["SPY"] and dw == 0
    assert w["SPY"] == pytest.approx(1 / 3) and w["CASH"] == pytest.approx(2 / 3)
    assert sum(w.values()) == pytest.approx(1.0)
    wn, _, _ = build_weights(r, cap_groups=None, slots="renorm", sigma=None, defensive=None)
    assert wn["SPY"] == 1.0 and wn["CASH"] == 0.0
    r0 = _row("2010-01-01", {}, trend={})
    assert build_weights(r0, cap_groups=None, slots="cashslot", sigma=None, defensive=None)[0]["CASH"] == pytest.approx(1.0)


# ---------- DEF ----------
def test_def_picks_ief_vs_cash():
    sess = _sessions()
    sig = sess[120]
    bars = _bars(sess, ief_total=0.10)
    rich = make_defensive_provider(bars, sess, _curve(sess, 0.0), "IEF", 3)
    assert rich(sig) == "IEF"
    # very high T-bill rate (20% discount) beats ~ +2.5% IEF 3m return
    high = make_defensive_provider(bars, sess, _curve(sess, 20.0), "IEF", 3)
    assert high(sig) == "CASH"
    # flat IEF vs zero rates: not strictly greater -> CASH
    flat = make_defensive_provider(_bars(sess, ief_total=0.0), sess, _curve(sess, 0.0), "IEF", 3)
    assert flat(sig) == "CASH"
    # weights: one risky asset, two defensive slots into IEF, and the IEF-selected-as-risky add-on
    r = _row(sig, {"SPY": 0.5}, trend={"SPY": 1})
    w, _, dw = build_weights(r, cap_groups=None, slots="def", sigma=None, defensive=rich)
    assert w["SPY"] == pytest.approx(1 / 3) and w["IEF"] == pytest.approx(2 / 3) and w["CASH"] == 0 and dw == pytest.approx(2 / 3)
    r2 = _row(sig, {"IEF": 0.5}, trend={"IEF": 1})
    w2, _, _ = build_weights(r2, cap_groups=None, slots="def", sigma=None, defensive=rich)
    assert w2["IEF"] == pytest.approx(1.0)
    w3, _, dw3 = build_weights(r, cap_groups=None, slots="def", sigma=None, defensive=high)
    assert w3["CASH"] == pytest.approx(2 / 3) and w3["IEF"] == 0 and dw3 == 0


def test_def_no_lookahead():
    sess = _sessions()
    sig = sess[120]
    curve_a = _curve(sess, 1.0)
    pct = pd.Series(1.0, index=sess)
    pct.iloc[119:] = 50.0   # rates on/after the day BEFORE signal close change; accrual at sig uses yield_as_of(sess[119])
    pct.iloc[120:] = 50.0
    a = make_defensive_provider(_bars(sess, 0.03), sess, curve_a, "IEF", 3).info(sig)
    # change only data strictly after signal close (prices and IRX): result identical
    pct2 = pd.Series(1.0, index=sess)
    pct2.iloc[121:] = 50.0
    b = make_defensive_provider(_bars(sess, 0.03, tail_mult=5.0), sess, CashYieldCurve(pct2), "IEF", 3).info(sig)
    assert a == b
    assert make_defensive_provider(_bars(sess, 0.03, tail_mult=5.0), sess, CashYieldCurve(pct2), "IEF", 3)(sig) == \
        make_defensive_provider(_bars(sess, 0.03), sess, curve_a, "IEF", 3)(sig)
    # the rate dated at the signal close itself must not matter either (accrual uses previous session's rate)
    pct3 = pd.Series(1.0, index=sess)
    pct3.iloc[120:] = 50.0
    c = make_defensive_provider(_bars(sess, 0.03), sess, CashYieldCurve(pct3), "IEF", 3).info(sig)
    assert c == a


# ---------- CAP ----------
def test_cap_skips_in_rank_order_and_one_per_group():
    ranked = ["SPY", "QQQ", "IWM", "EFA", "EEM", "GLD", "TLT", "IEF"]
    assert cap_select(ranked, GROUPS, 3) == ["SPY", "EFA", "GLD"]
    assert cap_select(["QQQ", "SPY", "TLT", "IEF", "EEM", "EFA"], GROUPS, 3) == ["QQQ", "TLT", "EEM"]
    assert cap_select(["SPY", "QQQ"], GROUPS, 3) == ["SPY"]
    rng = np.random.default_rng(1)
    for _ in range(50):
        mom = {s: float(rng.normal()) for s in UNIVERSE}
        trend = {s: bool(rng.integers(0, 2)) for s in UNIVERSE}
        w, sel, _ = build_weights(_row("2010-01-01", mom, trend), cap_groups=GROUPS, slots="renorm", sigma=None, defensive=None)
        g = [k for k, syms in GROUPS.items() for s in sel if s in syms]
        assert len(g) == len(set(g))
        assert sum(w.values()) == pytest.approx(1.0)
    # selection order = momentum rank among skipped-through
    r = _row("2010-01-01", {"SPY": 0.9, "QQQ": 0.8, "TLT": 0.7, "IWM": 0.6, "GLD": 0.5})
    _, sel, _ = build_weights(r, cap_groups=GROUPS, slots="renorm", sigma=None, defensive=None)
    assert sel == ["SPY", "TLT", "GLD"]


def test_cap_def_weights_sum_to_one():
    sess = _sessions()
    rich = make_defensive_provider(_bars(sess), sess, _curve(sess, 0.0), "IEF", 3)
    rng = np.random.default_rng(2)
    for i in range(60):
        mom = {s: float(rng.normal()) for s in UNIVERSE}
        trend = {s: bool(rng.integers(0, 2)) for s in UNIVERSE}
        for d in (rich, lambda s: "CASH"):
            w, _, _ = build_weights(_row(sess[120], mom, trend), cap_groups=GROUPS, slots="def", sigma=None, defensive=d)
            assert sum(w.values()) == pytest.approx(1.0)
            assert all(v >= 0 for v in w.values())


def test_ivol_applies_to_risky_share_only():
    sigmas = {"SPY": 0.01, "EFA": 0.02}
    r = _row("2010-01-01", {"SPY": 0.9, "EFA": 0.8}, trend={"SPY": 1, "EFA": 1})
    w, _, dw = build_weights(r, cap_groups=GROUPS, slots="def", sigma=lambda d, s: sigmas[s], defensive=lambda d: "IEF")
    assert w["SPY"] == pytest.approx(2 / 3 * 2 / 3) and w["EFA"] == pytest.approx(2 / 3 * 1 / 3)
    assert w["IEF"] == pytest.approx(1 / 3) and sum(w.values()) == pytest.approx(1.0)


# ---------- plumbing on real data ----------
@pytest.fixture(scope="module")
def train():
    return prepare_signals("train")


def test_cand_off_reproduces_base_weekly(train):
    _view, frames = train
    base = frames["W_M12_T40"]
    out = reselect3(combine_momentum([base], "X"), cap_groups=None, slots="renorm")
    assert [r["weights"] for r in out.rows] == [r["weights"] for r in base.rows]
    assert [r["selected_symbols"] for r in out.rows] == [r["selected_symbols"] for r in base.rows]


def test_step3_runs_end_to_end_small(train):
    view, frames = train
    curve = CashYieldCurve(load_irx())
    base = frames["M_M12_T40"]
    f = reselect3(base, cap_groups=GROUPS, slots="cashslot")
    nav, _reb, m = run_backtest_v3(view, f, V3_DEFAULT, curve)
    nav0, _r0, m0 = run_backtest_v3(view, reselect3(base, slots="renorm"), V3_DEFAULT, curve)
    assert np.allclose(nav0["nav"], run_backtest_v3(view, base, V3_DEFAULT, curve)[0]["nav"])
    assert m["mean_cash_weight"] >= m0["mean_cash_weight"]


# ---------- holdout guard ----------
def test_holdout_guard_still_enforced(train):
    view, frames = train
    with pytest.raises(HoldoutAccessError):
        run_backtest_v3(dataclasses.replace(view, partition="holdout"), frames["W_M12_T40"], V3_DEFAULT,
                        CashYieldCurve(load_irx()))
    from research_v3.step2 import _setup
    with pytest.raises(HoldoutAccessError):
        _setup("holdout")
    from research_v3.step3 import run_step3
    with pytest.raises(HoldoutAccessError):
        run_step3(["holdout"])
