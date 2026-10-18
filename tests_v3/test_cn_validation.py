"""Tests for research_v3/cn_transfer/validation (no network; snapshots are read from disk)."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from research_v2.contracts import SignalFrame
from research_v3.config import V3Config
from research_v3.cn_transfer import data as cn_data
from research_v3.cn_transfer.calendar_cn import get_xshg_calendar
from research_v3.cn_transfer.engine_cn import run_backtest_cn
from research_v3.cn_transfer.signals_cn import ShiborCurve
from research_v3.cn_transfer.validation import data_check as DC
from research_v3.cn_transfer.validation import robustness as RB
from research_v3.cn_transfer.validation import variants as V
from research_v3.cn_transfer.validation.protocol_val import (ProtocolChangedError, VAL_HASH_PATH, VAL_PROTOCOL_PATH,
                                                              load_validation_protocol)

ART = cn_data.DATA_DIR.parent / "artifacts"


# ------------------------------------------------------------------ protocol hash gate
def test_validation_protocol_loads_and_rejects_tampering(tmp_path):
    p = load_validation_protocol()
    assert VAL_HASH_PATH.read_text().strip().startswith("8c29800b")
    assert p["part_B_robustness"]["B1_parameter_neighbourhood"]["holds_if"].startswith("at least 7 of 9")
    bad = tmp_path / "p.json"
    bad.write_bytes(VAL_PROTOCOL_PATH.read_bytes() + b" ")
    with pytest.raises(ProtocolChangedError):
        load_validation_protocol(bad, VAL_HASH_PATH)
    wrong = tmp_path / "h.sha256"
    wrong.write_text("0" * 64)
    with pytest.raises(ProtocolChangedError):
        load_validation_protocol(VAL_PROTOCOL_PATH, wrong)


def test_cli_refuses_on_hash_mismatch(monkeypatch, tmp_path):
    from research_v3.cn_transfer.validation import __main__ as cli
    from research_v3.cn_transfer.validation import protocol_val as pv
    bad = tmp_path / "p.json"
    bad.write_bytes(VAL_PROTOCOL_PATH.read_bytes() + b"\n")
    monkeypatch.setattr(pv, "VAL_PROTOCOL_PATH", bad)
    monkeypatch.setattr(pv.load_validation_protocol, "__defaults__", (bad, VAL_HASH_PATH))
    with pytest.raises(ProtocolChangedError):
        cli.main(["run"])


# ------------------------------------------------------------------ wrapper == baseline at default settings
@pytest.fixture(scope="module")
def world():
    if not (cn_data.DATA_DIR / cn_data.MANIFEST).is_file() or not (ART / "nav_primary_CAP_DEF_M.csv").is_file():
        pytest.skip("baseline snapshot/artifacts missing")
    from research_v3.cn_transfer.validation.world import build_world
    return build_world()


def test_wrapper_reproduces_baseline_cap_def_m_nav_exactly(world):
    start = world.first_warm_start()
    assert start == "2014-09-01"
    _, capdef = world.frames(12, 40)
    nav, reb, _m = V.run_variant(world.cal, world.bars, start, world.score_end, capdef, RB.PRIMARY, world.curve,
                                 world.universe, world.sessions)           # lag=0, lots=None
    ref = pd.read_csv(ART / "nav_primary_CAP_DEF_M.csv", float_precision="round_trip")
    assert nav["date"].tolist() == ref["date"].tolist()
    assert np.array_equal(nav["nav"].to_numpy(), ref["nav"].to_numpy())
    assert np.array_equal(nav["cash"].to_numpy(), ref["cash"].to_numpy())
    # and the B1 (12, 40) cell is the same run
    res, _ = RB.evaluate(world, {"CAP_DEF_M": capdef}, RB.PRIMARY, start)
    assert res["CAP_DEF_M"]["FULL"]["excess_sharpe"] == pytest.approx(0.5904527113283927, abs=1e-12)


def test_b5_reconstruction_matches_nav_and_episode(world):
    start = world.first_warm_start()
    res, raw = RB.run_b0(world, start)
    nav, reb = raw["CAP_DEF_M"]
    b5 = RB.run_b5(world, start, nav, reb)
    assert b5["recon_max_abs_error"] < 1e-6
    ep = b5["episode"]
    assert ep["mdd"] == pytest.approx(res["CAP_DEF_M"]["FULL"]["max_drawdown"], abs=1e-12)
    assert ep["peak_date"] < ep["trough_date"] < ep["recovery_date"]
    # peak-to-trough contributions add up to the NAV change
    c = b5["peak_to_trough_contrib"]
    assert c["total"] == pytest.approx(c["nav_change_check"], abs=1e-6)
    assert c["total"] == pytest.approx(-ep["mdd"] * 100, abs=1e-6)


# ------------------------------------------------------------------ execution lag
SYM = ("AAA", "BBB")


def _toy_world(n_end="2016-12-30"):
    cal = get_xshg_calendar()
    sess = [s.strftime("%Y-%m-%d") for s in cal.sessions_in_range("2016-01-04", n_end)]
    rng = np.random.default_rng(1)
    bars = {}
    for k, s in enumerate(SYM):
        c = 10.0 * np.exp(np.cumsum(rng.normal(0.0003, 0.01, len(sess))))
        o = np.concatenate([[10.0], c[:-1]])
        bars[s] = pd.DataFrame({"Date": sess, "open": o, "high": np.maximum(o, c), "low": np.minimum(o, c), "close": c,
                                "adj_close": c, "adj_open": o, "volume": 1e6})
    curve = ShiborCurve(pd.Series(2.0, index=["2015-01-01"]))
    return cal, sess, bars, curve


def _frame(sess, sig_idx, w=None):
    rows = []
    for i in sig_idx:
        rows.append({"signal_session": sess[i], "execution_session": sess[i + 1], "status": "COMPLETE",
                     "weights": {"AAA": 0.5, "BBB": 0.5, "CASH": 0.0} if w is None else w, "def_bond_weight": 0.0})
    return SignalFrame(model_id="T", rows=rows, lineage=[])


def test_lag_offset_executes_exactly_k_sessions_later_and_after_signal_close():
    cal, sess, bars, curve = _toy_world()
    fr = _frame(sess, [20, 60, 100])
    for k in (0, 1, 2, 3, 4):
        lagged = V.apply_execution_lag(fr, sess, k)
        for a, b in zip(fr.rows, lagged.rows):
            assert sess.index(b["execution_session"]) == sess.index(a["execution_session"]) + k
            assert b["execution_session"] > b["signal_session"]
            assert b["signal_session"] == a["signal_session"]          # signal unchanged
        cfg = V3Config()
        nav, reb, _ = V.run_variant(cal, bars, sess[10], sess[-1], fr, cfg, curve, SYM, sess, lag=k)
        assert reb["execution_session"].tolist() == [sess[i + 1 + k] for i in (20, 60, 100)]
        assert (reb["execution_session"] > reb["signal_session"]).all()
    assert V.apply_execution_lag(fr, sess, 0) is fr
    with pytest.raises(ValueError):
        V.apply_execution_lag(fr, sess, -1)


def test_lag_drops_rows_beyond_last_session():
    cal, sess, bars, curve = _toy_world()
    fr = _frame(sess, [len(sess) - 3])           # executes at the 2nd-to-last session
    assert len(V.apply_execution_lag(fr, sess, 1).rows) == 1
    assert len(V.apply_execution_lag(fr, sess, 2).rows) == 0


# ------------------------------------------------------------------ lots + minimum commission
def test_trade_cost_min_commission_and_rate():
    c = V.LotCosts()
    assert V.trade_cost(0.0, c) == 0.0
    assert V.trade_cost(9900.0, c) == pytest.approx(5.0 + 4.95)          # min 5 CNY (2.5bp = 2.475) + 5bp slippage
    assert V.trade_cost(1_000_000.0, c) == pytest.approx(250.0 + 500.0)  # 2.5bp > 5 CNY
    assert V.trade_cost(20_000.0, c) == pytest.approx(5.0 + 10.0)        # 2.5bp = 5.0 exactly at 20k


def test_lot_rebalance_constructed_example():
    c = V.LotCosts()
    sh, cash, fee, vpre = V.lot_rebalance({}, 20000.0, {"A": 3.0, "B": 10.0}, {"A": 0.5, "B": 0.3}, ["A", "B"], c)
    assert vpre == 20000.0
    assert sh == {"A": 3300.0, "B": 600.0}                  # floor(10000/300)=33 lots, floor(6000/1000)=6 lots
    assert fee == pytest.approx((5 + 4.95) + (5 + 3.0))     # 9.95 + 8.00
    assert cash == pytest.approx(20000 - 9900 - 6000 - 17.95)
    # value conservation: post value = pre value - fee
    assert cash + 3300 * 3.0 + 600 * 10.0 == pytest.approx(20000 - fee)


def test_lot_rebalance_fee_shortfall_removes_a_lot_and_sells_fund_buys():
    c = V.LotCosts()
    # 1000 CNY, price 1.0, weight 1.0: 10 lots would cost 1000 + 5.5 fee -> cash -5.5 -> one lot removed
    sh, cash, fee, _ = V.lot_rebalance({}, 1000.0, {"A": 1.0}, {"A": 1.0}, ["A"], c)
    assert sh == {"A": 900.0}
    assert fee == pytest.approx(5.0 + 0.45)
    assert cash == pytest.approx(1000 - 900 - 5.45)
    # switch A -> B: sale proceeds fund the purchase, both legs pay at least the minimum commission
    sh, cash, fee, vpre = V.lot_rebalance({"A": 1000.0}, 50.0, {"A": 2.0, "B": 4.0}, {"B": 1.0}, ["A", "B"], c)
    assert vpre == pytest.approx(2050.0)
    assert sh["A"] == 0.0 and sh["B"] == 500.0              # floor(2050/400)=5 lots
    assert fee == pytest.approx((5 + 0.05 * 2000 / 100) + (5 + 0.05 * 2000 / 100))
    assert cash == pytest.approx(50 + 2000 - 2000 - fee)
    assert cash >= 0


def test_lot_engine_run_invariants():
    cal, sess, bars, curve = _toy_world()
    fr = _frame(sess, [20, 60, 100])
    lots = V.LotCosts()
    nav, reb, _ = V.run_variant(cal, bars, sess[10], sess[-1], fr, V3Config(initial_nav=20000.0), curve, SYM, sess, lots=lots)
    assert reb["fee"].iloc[0] >= 5.0 and (reb["fee"][reb["fee"] > 0] >= 5.0).all()   # min commission on every traded rebalance
    assert reb["V_post"].to_numpy() == pytest.approx((reb["V_pre"] - reb["fee"]).to_numpy())
    assert (nav["cash"] >= -1e-9).all()
    assert len(reb) == 3


# ------------------------------------------------------------------ Part A pure computations
def test_a1_diff_toy_example():
    dates = [f"2020-01-{d:02d}" for d in range(1, 11)]
    ref_r = np.full(9, 0.001)
    ours_r = ref_r.copy()
    ours_r[4] += 0.006                         # 60 bp on the move into dates[5]
    ours_r[7] += 0.0007                        # 7 bp on the move into dates[8]
    ref = pd.Series(100 * np.cumprod(np.concatenate([[1.0], 1 + ref_r])), index=dates)
    ours = pd.Series(100 * np.cumprod(np.concatenate([[1.0], 1 + ours_r])), index=dates)
    st = DC.a1_diff(ours, ref, [], start=dates[0], end=dates[-1])
    assert st["n_days"] == 9
    assert st["mean_abs_diff_bp"] == pytest.approx((60 + 7) / 9, rel=1e-9)
    assert st["max_abs_diff_bp"] == pytest.approx(60, rel=1e-9) and st["max_abs_diff_date"] == dates[5]
    assert st["n_days_gt_5bp"] == 2 and st["n_days_gt_50bp"] == 1 and st["n_days_gt_50bp_unexplained"] == 1
    # an event within 3 sessions explains it (offset 3), 4 sessions does not
    assert DC.a1_diff(ours, ref, [dates[8]], start=dates[0], end=dates[-1])["n_days_gt_50bp_unexplained"] == 0
    assert DC.a1_diff(ours, ref, [dates[9]], start=dates[0], end=dates[-1])["n_days_gt_50bp_unexplained"] == 1
    yrs = 9 / 365.25
    assert st["ann_ref"] == pytest.approx((1.001 ** 9) ** (1 / yrs) - 1)
    assert st["ann_diff_pp"] == pytest.approx((st["ann_ours"] - st["ann_ref"]) * 100)
    assert st["ann_ours"] > st["ann_ref"]
    assert not DC.a1_pass(st)                  # unexplained 60 bp day and a large annualised gap
    same = DC.a1_diff(ref, ref, [], start=dates[0], end=dates[-1])
    assert same["mean_abs_diff_bp"] == 0 and same["ann_diff_pp"] == 0 and DC.a1_pass(same)


def test_a1_uses_only_common_dates():
    ours = pd.Series([100.0, 101.0, 103.0, 104.0], index=["2020-01-01", "2020-01-02", "2020-01-03", "2020-01-06"])
    ref = pd.Series([50.0, 51.5, 52.0], index=["2020-01-01", "2020-01-03", "2020-01-06"])   # 01-02 missing in ref
    common, ra, rb = DC.aligned_returns(ours, ref, "2020-01-01", "2020-01-31")
    assert common == ["2020-01-01", "2020-01-03", "2020-01-06"]
    assert ra[0] == pytest.approx(103 / 100 - 1) and rb[0] == pytest.approx(51.5 / 50 - 1)


def test_nav_total_return_index_and_event_matching():
    nav = pd.DataFrame({"净值日期": ["2020-01-01", "2020-01-02", "2020-01-03"], "日增长率": [1.0, -0.5, 2.0]})
    idx = DC.nav_total_return_index(nav)
    assert idx.iloc[-1] == pytest.approx(1.01 * 0.995 * 1.02)
    sess = [f"2020-01-{d:02d}" for d in range(1, 21)]
    m = DC.match_events([("2020-01-05", 0.05), ("2020-01-10", 0.07), ("2020-01-15", 0.02)],
                        [("2020-01-05", 0.05), ("2020-01-12", 0.07), ("2020-01-18", 0.09)], sess)
    assert len(m["exact"]) == 1 and len(m["offset"]) == 1 and m["offset"][0][4] == 2
    assert m["only_sina"] == [("2020-01-15", 0.02)] and m["only_em"] == [("2020-01-18", 0.09)]


# ------------------------------------------------------------------ Part B pure helpers
def test_cell_check_and_drawdown_episode():
    strat = {"FULL": {"excess_sharpe": 0.5, "max_drawdown": 0.30}}
    bench = {"CN_60_40": {"FULL": {"excess_sharpe": 0.3}}, "CSI300_BH": {"FULL": {"max_drawdown": 0.45}}}
    assert RB.cell_check(strat, bench)["pass"]
    assert not RB.cell_check({"FULL": {"excess_sharpe": 0.2, "max_drawdown": 0.30}}, bench)["pass"]
    assert not RB.cell_check({"FULL": {"excess_sharpe": 0.5, "max_drawdown": 0.50}}, bench)["pass"]
    nav = pd.DataFrame({"date": [f"d{i}" for i in range(6)], "nav": [110.0, 120.0, 90.0, 80.0, 100.0, 125.0]})
    ep = RB.max_drawdown_episode(nav, 100.0)
    assert ep["peak_date"] == "d1" and ep["trough_date"] == "d3" and ep["recovery_date"] == "d5"
    assert ep["mdd"] == pytest.approx(1 - 80 / 120)
