from __future__ import annotations

import json
import shutil

import pandas as pd
import pytest

from research_v2.calendar import get_trading_calendar
from research_v2.contracts import DatasetManifest, DatasetView, SignalFrame, UNIVERSE
from research_v2.engine import run_backtest

from research_v3.cash_yield import (CashYieldCurve, CashYieldError, DATA_DIR, IRX_CSV, IRX_MANIFEST,
                                    discount_to_bey, load_irx)
from research_v3.config import V2_EQUIVALENT, V3Config
from research_v3.engine import run_backtest_v3
from research_v3.guards import HoldoutAccessError, assert_partition_allowed
from research_v3.runner import prepare_signals


# ---------------- synthetic fixtures ----------------
START, END = "2010-03-01", "2010-03-12"


def _sessions():
    cal = get_trading_calendar()
    return [s.strftime("%Y-%m-%d") for s in cal.sessions_in_range(pd.Timestamp(START), pd.Timestamp(END))]


def _view(partition="train", end=END, flat=False):
    sess = _sessions()
    bars = {}
    for k, sym in enumerate(UNIVERSE):
        p = [100.0 * (1 + 0.01 * ((i + k) % 3)) if not flat else 100.0 for i in range(len(sess))]
        bars[sym] = pd.DataFrame({"Date": sess, "adj_open": p, "adj_close": [x * 1.001 for x in p]})
    m = DatasetManifest("x", "", "", "", {})
    return DatasetView(manifest=m, partition=partition, as_of="2010-03-12T16:01:00-05:00",
                       feature_start="2005-01-03", score_start=START, score_end=end, bars=bars)


def _signals(sess, held_from=2, weights=None):
    rows = [] if weights is None else [{
        "period_id": "p", "signal_session": sess[held_from - 1], "execution_session": sess[held_from],
        "status": "COMPLETE", "weights": weights}]
    return SignalFrame(model_id="T", rows=rows, lineage=[])


def _curve(pct=2.0):
    sess = _sessions()
    return CashYieldCurve(pd.Series([pct] * len(sess), index=sess))


# ---------------- regression vs v2 ----------------
@pytest.fixture(scope="module")
def train():
    return prepare_signals("train")


@pytest.mark.parametrize("model_id", ["W_M12_T40", "M_M12_T40"])
def test_regression_equals_v2_engine(train, model_id):
    view, frames = train
    sf = frames[model_id]
    nav2, _, _, reb2, _, m2 = run_backtest(view, sf, cost_bps=5.0, initial_nav=100000.0)
    nav3, reb3, m3 = run_backtest_v3(view, sf, V2_EQUIVALENT, curve=None)
    assert len(nav2) == len(nav3) > 2000
    assert (nav2["nav"] - nav3["nav"]).abs().max() < 1e-9
    assert (nav2["cash"] - nav3["cash"]).abs().max() < 1e-9
    assert len(reb2) == len(reb3) > 50
    assert m2["cagr"] == pytest.approx(m3["cagr"], abs=1e-12)
    assert (nav3["cash_interest"] == 0).all()


# ---------------- cash accrual ----------------
def test_discount_to_bey_known_value():
    # d=2%: (0.02*365/360)/(1-0.02*91/360) = 0.0202778/0.994944
    assert discount_to_bey(2.0) == pytest.approx(0.0202778 / 0.9949444, rel=1e-5)
    assert discount_to_bey(0.0) == 0.0


def test_cash_growth_all_cash_known_rate():
    view = _view()
    sess = _sessions()
    cfg = V3Config(cost_bps=5, slippage_bps=5, cash_yield_source="irx")
    nav, _, m = run_backtest_v3(view, _signals(sess), cfg, _curve(2.0))
    y = discount_to_bey(2.0)
    expected = 100000.0
    assert nav["cash_interest"].iloc[0] == 0.0  # first session: no previous session
    for i in range(1, len(sess)):
        days = (pd.Timestamp(sess[i]) - pd.Timestamp(sess[i - 1])).days
        expected *= 1 + y * days / 365.0
        assert nav["nav"].iloc[i] == pytest.approx(expected, rel=1e-12)
    # weekend gap accrues 3 calendar days
    assert (pd.Timestamp(sess[5]) - pd.Timestamp(sess[4])).days == 3


def test_cash_yield_none_is_flat():
    nav, _, _ = run_backtest_v3(_view(), _signals(_sessions()), V2_EQUIVALENT)
    assert (nav["nav"] == 100000.0).all()


def test_no_lookahead_rate_change_on_day_d():
    """Accrual credited at session d uses the rate dated d-1. Changing the rate dated d must not change
    NAV at d (it first affects NAV at d+1). Changing the rate dated d-1 does change NAV at d."""
    sess = _sessions()
    cfg = V3Config(cash_yield_source="irx")
    base = pd.Series([2.0] * len(sess), index=sess)
    d = 4
    chg = base.copy()
    chg[sess[d]] = 5.0
    n0, _, _ = run_backtest_v3(_view(), _signals(sess), cfg, CashYieldCurve(base))
    n1, _, _ = run_backtest_v3(_view(), _signals(sess), cfg, CashYieldCurve(chg))
    assert (n0["nav"].iloc[: d + 1] == n1["nav"].iloc[: d + 1]).all()
    assert n1["nav"].iloc[d + 1] > n0["nav"].iloc[d + 1]
    chg2 = base.copy()
    chg2[sess[d - 1]] = 5.0
    n2, _, _ = run_backtest_v3(_view(), _signals(sess), cfg, CashYieldCurve(chg2))
    assert n2["nav"].iloc[d] > n0["nav"].iloc[d]
    assert (n0["nav"].iloc[:d] == n2["nav"].iloc[:d]).all()


def test_forward_fill_only_from_earlier_and_fail_if_none():
    c = CashYieldCurve(pd.Series([1.0, float("nan"), 3.0], index=["2010-03-01", "2010-03-02", "2010-03-03"]))
    assert c.pct_as_of("2010-03-02") == 1.0  # NaN filled from earlier
    assert c.pct_as_of("2010-03-03") == 3.0
    assert c.pct_as_of("2010-03-10") == 3.0
    with pytest.raises(CashYieldError):
        c.pct_as_of("2010-02-28")  # nothing earlier; later values must not be backfilled


def test_irx_hash_verified(tmp_path):
    for f in (IRX_CSV, IRX_MANIFEST):
        shutil.copy(DATA_DIR / f, tmp_path / f)
    assert len(load_irx(tmp_path)) > 4000
    with open(tmp_path / IRX_CSV, "a") as fh:
        fh.write("2020-12-31,9.99\n")
    with pytest.raises(CashYieldError, match="hash mismatch"):
        load_irx(tmp_path)


def test_irx_snapshot_has_no_holdout_dates():
    assert load_irx().index.max() <= "2020-12-31"


# ---------------- slippage ----------------
W = {"SPY": 0.5, "QQQ": 0.5}


def test_slippage_strictly_lowers_nav_when_trading():
    sess = _sessions()
    navs = []
    for slip in (0, 5, 20):
        n, r, _ = run_backtest_v3(_view(), _signals(sess, weights=W),
                                  V3Config(cost_bps=5, slippage_bps=slip, cash_yield_source="none"))
        assert len(r) == 1
        navs.append(n["nav"].iloc[-1])
    assert navs[0] > navs[1] > navs[2]


def test_slippage_equals_cost_bps_sum():
    sess = _sessions()
    a, _, _ = run_backtest_v3(_view(), _signals(sess, weights=W), V3Config(5, 5, "none"))
    b, _, _ = run_backtest_v3(_view(), _signals(sess, weights=W), V3Config(10, 0, "none"))
    assert (a["nav"] - b["nav"]).abs().max() < 1e-9


def test_slippage_no_effect_without_trades():
    sess = _sessions()
    a, _, _ = run_backtest_v3(_view(), _signals(sess), V3Config(5, 0, "none"))
    b, _, _ = run_backtest_v3(_view(), _signals(sess), V3Config(5, 50, "none"))
    assert (a["nav"] == b["nav"]).all()


# ---------------- holdout guard ----------------
@pytest.mark.parametrize("p", ["historical_holdout", "prospective_oos", "holdout", "warmup", "bogus"])
def test_guard_rejects_non_train_validation(p):
    with pytest.raises(HoldoutAccessError):
        assert_partition_allowed(p)


def test_guard_in_runner_and_engine():
    with pytest.raises(HoldoutAccessError):
        prepare_signals("historical_holdout")
    with pytest.raises(HoldoutAccessError):
        run_backtest_v3(_view(partition="historical_holdout"), _signals(_sessions()), V2_EQUIVALENT)
    with pytest.raises(HoldoutAccessError):  # allowed name but out-of-range end date
        run_backtest_v3(_view(end="2021-01-05"), _signals(_sessions()), V2_EQUIVALENT)


def test_config_validation():
    with pytest.raises(ValueError):
        V3Config(cash_yield_source="libor")
