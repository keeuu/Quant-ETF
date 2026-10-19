"""Tests for research_v3/cn_transfer (no network; research_v2 snapshot and cn_transfer data are read from disk)."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from research_v2.calendar import get_trading_calendar, market_as_of_after_close
from research_v2.contracts import UNIVERSE, SignalFrame
from research_v2.schedule import build_monthly_matched_schedule, build_weekly_schedule
from research_v2.signals import compute_matched_monthly_signals, compute_model_signals_for_events
from research_v3.cash_yield import CashYieldCurve, CashYieldError
from research_v3.config import V3Config, V3_DEFAULT
from research_v3.engine import run_backtest_v3
from research_v3.signals_step3 import build_weights, make_defensive_provider, reselect3
from research_v3.cn_transfer import data as cn_data
from research_v3.cn_transfer import protocol as cn_protocol
from research_v3.cn_transfer.calendar_cn import (CN_TZ, build_monthly_matched_schedule_cal,
                                                  build_weekly_schedule_cal, get_xshg_calendar,
                                                  market_as_of_after_close_cal)
from research_v3.cn_transfer.engine_cn import run_backtest_cn
from research_v3.cn_transfer.runner import classify, strategy_frames
from research_v3.cn_transfer.signals_cn import (ShiborCurve, build_weights_cn, cap_def_frame,
                                                compute_model_signals_cn)

ROOT = Path(__file__).resolve().parents[1]
CN = ("510300", "510050", "510500", "159915", "518880", "511010")
CN_GROUPS = {"CN_LARGE": ["510300", "510050"], "CN_MIDSMALL_GROWTH": ["510500", "159915"],
             "GOLD": ["518880"], "BONDS": ["511010"]}
# order-preserving relabelling of the CN universe into US tickers (tie-break order identical)
CN2US = {"510300": "SPY", "510050": "QQQ", "510500": "IWM", "159915": "EFA", "518880": "GLD", "511010": "IEF"}
US_GROUPS = {"A": ["SPY", "QQQ"], "B": ["IWM", "EFA"], "GOLD": ["GLD"], "BONDS": ["IEF", "TLT"]}


# ------------------------------------------------------------------ protocol / data hash gates
def test_protocol_loads_and_rejects_tampering(tmp_path):
    p = cn_protocol.load_verified()
    assert p["mapping_from_us_to_cn"]["universe_order"] == list(CN)
    bad = tmp_path / "p.json"
    bad.write_bytes(cn_protocol.PROTOCOL_PATH.read_bytes() + b" ")
    with pytest.raises(cn_protocol.ProtocolChangedError):
        cn_protocol.load_verified(bad, cn_protocol.HASH_PATH)


def _tiny_snapshot(d: Path) -> None:
    d.mkdir()
    (d / "a.csv").write_text("Date,x\n2020-01-02,1\n")
    (d / "manifest.json").write_text(json.dumps({"files": {"a.csv": {"sha256": cn_data.sha256_file(d / "a.csv")}}}))


def test_data_manifest_hash_rejection(tmp_path):
    d = tmp_path / "snap"
    _tiny_snapshot(d)
    assert "files" in cn_data.verify_manifest(d)
    (d / "a.csv").write_text("Date,x\n2020-01-02,2\n")
    with pytest.raises(cn_data.CNDataError, match="hash mismatch"):
        cn_data.verify_manifest(d)
    (d / "a.csv").unlink()
    with pytest.raises(cn_data.CNDataError, match="missing"):
        cn_data.verify_manifest(d)


def test_committed_snapshot_verifies():
    if not (cn_data.DATA_DIR / cn_data.MANIFEST).is_file():
        pytest.skip("snapshot not created")
    cn_data.verify_manifest()


# ------------------------------------------------------------------ XSHG schedule adapter equivalence
def _us_sessions(end="2020-12-31"):
    st = pd.read_csv(ROOT / "research_v2/data/snapshot_20260918/session_table.csv")
    return [s for s in st["session_date"].tolist() if s <= end]


def test_schedule_adapter_reproduces_v2_on_xnys():
    cal = get_trading_calendar()
    sessions = _us_sessions()
    as_of = market_as_of_after_close("2020-12-31")
    assert market_as_of_after_close_cal(cal, "2020-12-31", __import__("zoneinfo").ZoneInfo("America/New_York")) == as_of
    w2 = build_weekly_schedule(sessions, as_of=as_of)
    wc = build_weekly_schedule_cal(cal, sessions, as_of)
    assert wc == w2 and len(w2) > 800
    m2 = build_monthly_matched_schedule(sessions, as_of=as_of, weekly_events=w2)
    mc = build_monthly_matched_schedule_cal(cal, sessions, as_of, wc)
    assert mc == m2 and len(m2) > 150
    # truncated sample + incomplete last period (pending execution) is handled identically
    s2 = [s for s in sessions if s <= "2018-06-27"]
    a2 = market_as_of_after_close("2018-06-27")
    w2 = build_weekly_schedule(s2, as_of=a2)
    assert build_weekly_schedule_cal(cal, s2, a2) == w2
    assert build_monthly_matched_schedule_cal(cal, s2, a2, w2) == build_monthly_matched_schedule(s2, a2, w2)


def test_xshg_calendar_basics():
    cal = get_xshg_calendar()
    assert cal.is_session("2026-09-30") and not cal.is_session("2026-10-01")
    assert cal.next_session(pd.Timestamp("2026-09-30")).strftime("%Y-%m-%d") == "2026-10-08"
    assert cal.session_close(pd.Timestamp("2016-02-01")).astimezone(CN_TZ).hour == 15


# ------------------------------------------------------------------ synthetic CN dataset helpers
def _synthetic_cn_bars(seed=7, start="2013-07-29", end="2016-12-30"):
    cal = get_xshg_calendar()
    sess = [s.strftime("%Y-%m-%d") for s in cal.sessions_in_range(start, end)]
    rng = np.random.default_rng(seed)
    bars = {}
    for k, sym in enumerate(CN):
        drift = [0.0006, 0.0003, 0.0005, 0.0007, 0.0004, 0.0002][k]
        r = rng.normal(drift, 0.01 + 0.002 * k, len(sess))
        close = 100.0 * np.exp(np.cumsum(r))
        opn = np.concatenate([[100.0], close[:-1]]) * (1 + rng.normal(0, 0.002, len(sess)))
        bars[sym] = pd.DataFrame({"Date": sess, "open": opn, "high": np.maximum(opn, close) * 1.001,
                                  "low": np.minimum(opn, close) * 0.999, "close": close, "adj_close": close,
                                  "adj_open": opn, "volume": 1e6})
    return bars, sess


def _flat_rate(sess, pct=3.0):
    return pd.Series(pct, index=["2013-01-01"] + sess[::50]).sort_index()


# ------------------------------------------------------------------ CN signals == research_v3 step3 CAP_DEF
def test_build_weights_cn_matches_step3_build_weights_on_random_rows():
    rng = np.random.default_rng(3)
    for trial in range(300):
        mom = {s: (float(rng.choice([-0.1, 0.0, 0.05, 0.05, 0.2, np.nan])) if rng.random() > 0.1 else None) for s in CN}
        tr = {s: bool(rng.random() > 0.35) for s in CN}
        row_cn = {"signal_session": "2015-01-30", "momentum_scores": mom, "trend_passes": tr}
        row_us = {"signal_session": "2015-01-30",
                  "momentum_scores": {**{u: None for u in UNIVERSE}, **{CN2US[s]: v for s, v in mom.items()}},
                  "trend_passes": {**{u: False for u in UNIVERSE}, **{CN2US[s]: v for s, v in tr.items()}}}
        for choose_bond in (True, False):
            wcn, selcn, dcn = build_weights_cn(row_cn, universe=CN, cap_groups=CN_GROUPS, slots="def",
                                               defensive=lambda s: "511010" if choose_bond else "CASH",
                                               top_n=3, def_symbol="511010")
            wus, selus, dus = build_weights(row_us, cap_groups=US_GROUPS, slots="def", sigma=None,
                                            defensive=lambda s: "IEF" if choose_bond else "CASH", top_n=3,
                                            def_symbol="IEF")
            assert [CN2US[s] for s in selcn] == selus
            assert dcn == dus
            for s in CN:
                assert wcn[s] == pytest.approx(wus[CN2US[s]])
            assert wcn["CASH"] == pytest.approx(wus["CASH"])
            assert sum(wcn.values()) == pytest.approx(1.0)


def test_cn_pipeline_equals_step3_cap_def_on_synthetic_dataset():
    bars, sess = _synthetic_cn_bars()
    cal = get_xshg_calendar()
    curve = ShiborCurve(_flat_rate(sess, 3.0))
    as_of = market_as_of_after_close_cal(cal, sess[-1], CN_TZ)
    weekly = build_weekly_schedule_cal(cal, sess, as_of)
    monthly = build_monthly_matched_schedule_cal(cal, sess, as_of, weekly)
    # CN pipeline
    wf = compute_model_signals_cn(bars, CN, sess, weekly, "W", 12, 40)
    mf = compute_matched_monthly_signals(wf, monthly, "M")
    dcn = make_defensive_provider(bars, sess, curve, symbol="511010", months=3)
    cf = cap_def_frame(mf, "CAP_DEF_M", universe=CN, cap_groups=CN_GROUPS, defensive=dcn, def_symbol="511010")
    # reference: research_v2 / research_v3 step-3 code on the same series relabelled to US tickers; two fillers
    # (declining => trend fails => never eligible) complete the 8-asset US universe
    us_bars = {CN2US[s]: b.copy() for s, b in bars.items()}
    decline = np.linspace(100.0, 50.0, len(sess))
    for filler in ("EEM", "TLT"):
        us_bars[filler] = pd.DataFrame({"Date": sess, "open": decline, "high": decline, "low": decline,
                                        "close": decline, "adj_close": decline, "adj_open": decline, "volume": 1.0})
    wf_us = compute_model_signals_for_events(us_bars, sess, weekly, "W", 12, 40)
    mf_us = compute_matched_monthly_signals(wf_us, monthly, "M")
    dus = make_defensive_provider(us_bars, sess, curve, symbol="IEF", months=3)
    ref = reselect3(mf_us, "CAP_DEF_M", cap_groups=US_GROUPS, slots="def", defensive=dus, top_n=3)
    assert len(cf.rows) == len(ref.rows) > 20
    nonflat = 0
    for a, b in zip(cf.rows, ref.rows):
        assert a["signal_session"] == b["signal_session"] and a["execution_session"] == b["execution_session"]
        assert [CN2US[s] for s in a["selected_symbols"]] == b["selected_symbols"]
        for s in CN:
            assert a["weights"][s] == pytest.approx(b["weights"][CN2US[s]])
        assert a["weights"]["CASH"] == pytest.approx(b["weights"]["CASH"])
        assert a["def_bond_weight"] == pytest.approx(b["def_ief_weight"])
        nonflat += len(a["selected_symbols"]) > 0
    assert nonflat > 5


def test_cn_signal_mirror_equals_v2_on_us_universe():
    """compute_model_signals_cn with the US universe reproduces research_v2 compute_model_signals_for_events."""
    from research_v3.step2 import _setup
    view, sessions, weekly, monthly = _setup("validation")
    a = compute_model_signals_cn(view.bars, UNIVERSE, sessions, weekly, "W_M12_T40", 12, 40)
    b = compute_model_signals_for_events(view.bars, sessions, weekly, "W_M12_T40", 12, 40)
    assert a.rows == b.rows and a.lineage == b.lineage and len(a.rows) > 200


# ------------------------------------------------------------------ engine equivalence on US data
def test_cn_engine_reproduces_v3_engine_on_us_data():
    from research_v3.cash_yield import load_irx
    from research_v3.step2 import _setup
    view, sessions, weekly, monthly = _setup("validation")
    wf = compute_model_signals_for_events(view.bars, sessions, weekly, "W_M12_T40", 12, 40)
    mf = compute_matched_monthly_signals(wf, monthly, "M_M12_T40")
    curve = CashYieldCurve(load_irx())
    for frame in (wf, mf):
        n1, r1, m1 = run_backtest_v3(view, frame, V3_DEFAULT, curve)
        n2, r2, m2 = run_backtest_cn(get_trading_calendar(), view.bars, view.score_start, view.score_end, frame,
                                     V3_DEFAULT, curve, UNIVERSE)
        pd.testing.assert_frame_equal(n1, n2)
        pd.testing.assert_frame_equal(r1, r2)
        assert m1 == m2


# ------------------------------------------------------------------ SHIBOR accrual
def test_shibor_accrual_uses_previous_session_rate_simple_convention():
    # rate known at d0 = 2.0, d1 = 4.0, d2 = 8.0 (percent). Session pair (d0 -> d1) must use 2.0, (d1 -> d2) 4.0.
    curve = ShiborCurve(pd.Series({"2020-01-02": 2.0, "2020-01-03": 4.0, "2020-01-06": 8.0}))
    assert curve.accrual_factor("2020-01-02", "2020-01-03") == pytest.approx(1 + 0.02 * 1 / 365)
    assert curve.accrual_factor("2020-01-03", "2020-01-06") == pytest.approx(1 + 0.04 * 3 / 365)  # weekend accrues
    # forward fill from earlier values only (no rate on 01-07 -> 8.0 from 01-06; never a later value)
    assert curve.accrual_factor("2020-01-07", "2020-01-08") == pytest.approx(1 + 0.08 / 365)
    with pytest.raises(CashYieldError):
        curve.accrual_factor("2019-12-31", "2020-01-02")  # no value on or before 2019-12-31


def test_engine_cash_interest_uses_previous_session_shibor():
    bars, sess = _synthetic_cn_bars(start="2020-03-02", end="2020-03-13")
    rates = pd.Series({sess[0]: 2.0, sess[1]: 4.0, sess[2]: 8.0, sess[3]: 16.0}).reindex(sess).ffill()
    curve = ShiborCurve(rates.dropna())
    empty = SignalFrame("none", [], [])
    nav, _reb, _m = run_backtest_cn(get_xshg_calendar(), bars, sess[0], sess[-1], empty,
                                    V3Config(5, 5, "irx"), curve, CN)
    assert nav["cash_interest"].iloc[0] == 0.0     # first session accrues nothing
    expect = 100000.0
    for i in range(1, len(sess)):
        days = (pd.Timestamp(sess[i]) - pd.Timestamp(sess[i - 1])).days
        expect *= 1 + float(rates.loc[sess[i - 1]]) / 100.0 * days / 365.0
        assert nav["nav"].iloc[i] == pytest.approx(expect, rel=1e-12)


# ------------------------------------------------------------------ benchmarks plumbing
def test_benchmark_frames_and_nav_paths():
    bars, sess = _synthetic_cn_bars()
    cal = get_xshg_calendar()
    curve = ShiborCurve(_flat_rate(sess, 3.0))
    as_of = market_as_of_after_close_cal(cal, sess[-1], CN_TZ)
    weekly = build_weekly_schedule_cal(cal, sess, as_of)
    monthly = build_monthly_matched_schedule_cal(cal, sess, as_of, weekly)
    mf = compute_matched_monthly_signals(compute_model_signals_cn(bars, CN, sess, weekly, "W", 12, 40), monthly, "M")
    score_start = mf.rows[0]["execution_session"]
    fr = strategy_frames(mf, mf, score_start, sess[-1], CN)
    assert len(fr["CSI300_BH"].rows) == 1 and fr["CSI300_BH"].rows[0]["execution_session"] == score_start
    assert fr["CSI300_BH"].rows[0]["weights"]["510300"] == 1.0
    assert len(fr["CN_60_40"].rows) == sum(r["status"] == "COMPLETE" for r in mf.rows)  # PENDING tail excluded
    for r in fr["CN_60_40"].rows:
        assert r["weights"]["510300"] == 0.6 and r["weights"]["511010"] == 0.4 and sum(
            v for k, v in r["weights"].items() if k != "CASH") == pytest.approx(1.0)
    cfg = V3Config(5, 5, "irx")
    nav, reb, _ = run_backtest_cn(cal, bars, score_start, sess[-1], fr["CSI300_BH"], cfg, curve, CN)
    # CSI300_BH tracks the 510300 adjusted path after the entry day
    px = bars["510300"].set_index("Date")["adj_close"]
    ratio = nav.set_index("date")["nav"] / px.reindex(nav["date"]).values
    assert ratio.iloc[1:].std() / ratio.iloc[1:].mean() < 1e-12
    c = 10.0 / 1e4
    o0, c0 = bars["510300"].set_index("Date").loc[score_start, ["adj_open", "adj_close"]]
    assert nav["nav"].iloc[0] == pytest.approx(100000.0 / (1 + c) * c0 / o0, rel=1e-12)
    assert len(reb) == 1
    # 60/40: entry-day NAV with exact 60/40 split at the open
    nav6, reb6, _ = run_backtest_cn(cal, bars, score_start, sess[-1], fr["CN_60_40"], cfg, curve, CN)
    b = {s: bars[s].set_index("Date").loc[score_start] for s in ("510300", "511010")}
    v_post = 100000.0 / (1 + c)
    exp = v_post * (0.6 * b["510300"]["adj_close"] / b["510300"]["adj_open"]
                    + 0.4 * b["511010"]["adj_close"] / b["511010"]["adj_open"])
    assert nav6["nav"].iloc[0] == pytest.approx(exp, rel=1e-12)
    assert len(reb6) == len(fr["CN_60_40"].rows)


# ------------------------------------------------------------------ outcome classification
def _tbl(x):  # x: dict[(part, strat)] -> (xsharpe, mdd)
    return pd.DataFrame([{"partition": p, "strategy": s, "excess_sharpe": v[0], "max_drawdown": v[1]}
                         for (p, s), v in x.items()])


def test_classify_outcomes():
    def mk(c1, c2, c3a, c3b):
        d = {}
        for p in ("FULL", "SUB_A", "SUB_B"):
            d[(p, "CN_60_40")] = (1.0, 0.2)
            d[(p, "CAP_DEF_M")] = (1.1 if (c1 if p == "FULL" else c3a if p == "SUB_A" else c3b) else 0.9, 0.1 if c2 else 0.4)
            d[(p, "CSI300_BH")] = (0.5, 0.3)
        return classify(_tbl(d))["outcome"]
    assert mk(1, 1, 1, 1) == "SUPPORTED"
    assert mk(1, 1, 0, 1) == "PARTIAL"
    assert mk(0, 1, 1, 1) == "PARTIAL"
    assert mk(1, 0, 1, 1) == "PARTIAL"
    assert mk(0, 0, 1, 1) == "NOT_SUPPORTED"


# ------------------------------------------------------------------ self-built total-return adjustment
def _bars(dates, closes, opens=None):
    opens = opens or closes
    return pd.DataFrame({"Date": dates, "open": opens, "high": closes, "low": closes, "close": closes, "volume": 1.0})


def test_adjustment_dividend_on_ex_date_gives_price_return_plus_yield():
    dates = ["2020-01-02", "2020-01-03", "2020-01-06", "2020-01-07"]
    bars = _bars(dates, [10.0, 10.0, 9.5, 9.6], opens=[10.0, 10.0, 9.45, 9.5])
    # D = 0.5 per share at ex-date 2020-01-06 (price falls by the distribution)
    out = cn_data.build_adjusted(bars, [("2020-01-06", 0.5)], [("2020-01-06", 1.0)])
    ret = out["adj_close"].pct_change()
    assert ret.iloc[2] == pytest.approx(9.5 / 10.0 - 1.0 + 0.5 / 10.0)           # price return + D / prev close
    assert ret.iloc[2] == pytest.approx(0.0)                                      # economically flat day
    assert ret.iloc[1] == 0.0 and ret.iloc[3] == pytest.approx(9.6 / 9.5 - 1.0)  # unaffected outside ex-date
    assert out["adj_open"].iloc[2] == pytest.approx(9.45 * out["adj_close"].iloc[2] / 9.5)
    assert out["adj_close"].iloc[0] == 10.0


def test_adjustment_cumulative_dividend_increments_and_share_conversion():
    dates = [f"2020-01-{d:02d}" for d in (2, 3, 6, 7, 8, 9)]
    closes = [10.0, 10.0, 9.0, 9.0, 18.0, 18.0]   # D=1 at 01-06; 1:2 share consolidation (price x2) recorded 01-07
    bars = _bars(dates, closes)
    # cumulative dividends: 1.0 then still 1.0 (increment 0); share factor s: 1 -> 0.5 at 01-07 (aligns to 01-08)
    out = cn_data.build_adjusted(bars, [("2020-01-06", 1.0), ("2020-01-07", 1.0)],
                                 [("2020-01-06", 1.0), ("2020-01-07", 0.5)])
    ret = out["adj_close"].pct_change().round(12).tolist()
    assert ret[1:] == [0.0, 0.0, 0.0, 0.0, 0.0]


def test_parse_hfq_js():
    txt = ('var x={"total":2,"data":[{"d":"2020-01-06", "f":"1", "s":"1.0", "u":"0.5"},'
           '{"d":"1900-01-01", "f":"1", "s":"1.0", "u":"0.0"}]}\n/* sig */')
    div, sh = cn_data.parse_hfq_js(txt)
    assert div == [("2020-01-06", 0.5)] and sh == [("2020-01-06", 1.0)]


# ------------------------------------------------------------------ amendment 1: suspensions
def _amend_setup():
    from research_v3.cn_transfer.engine_cn import run_backtest_cn  # noqa: F401
    bars, sess = _synthetic_cn_bars(start="2020-03-02", end="2020-04-30")
    curve = ShiborCurve(pd.Series({"2020-01-01": 2.0}))
    cfg = V3Config(5, 5, "irx")
    return bars, sess, curve, cfg


def _frame(sess_exec, sig, weights):
    w = {s: 0.0 for s in CN}
    w["CASH"] = 0.0
    w.update(weights)
    return SignalFrame("t", [{"signal_session": sig, "execution_session": sess_exec, "status": "COMPLETE",
                              "weights": w}], [])


def test_amendment_loads_and_rejects_tampering(tmp_path):
    a = cn_protocol.load_amendment_verified()
    assert a["amendment"] == 1
    bad = tmp_path / "a.json"
    bad.write_bytes(cn_protocol.AMENDMENT_PATH.read_bytes() + b" ")
    with pytest.raises(cn_protocol.ProtocolChangedError):
        cn_protocol.load_amendment_verified(bad, cn_protocol.AMENDMENT_HASH_PATH)


def test_valuation_carry_forward_and_no_lookahead():
    from research_v2.engine import EngineError
    bars, sess, curve, cfg = _amend_setup()
    cal = get_xshg_calendar()
    fr = _frame(sess[1], sess[0], {"510300": 1.0})
    gap = sess[5]
    cut = {k: v[v.Date != gap].reset_index(drop=True) for k, v in bars.items() if k == "510300"}
    b2 = {**bars, "510300": cut["510300"]}
    with pytest.raises(EngineError):   # original protocol stays fail-closed
        run_backtest_cn(cal, b2, sess[0], sess[-1], fr, cfg, curve, CN)
    nav, _r, m = run_backtest_cn(cal, b2, sess[0], sess[-1], fr, cfg, curve, CN, suspension_rules=True)
    n = nav.set_index("date")["nav"]
    assert n[gap] == pytest.approx(n[sess[4]])               # valued at last close (zero cash interest on invested)
    assert m["carried_valuation_days"] == {"510300": 1}
    # no look-ahead: changing every price AFTER the suspended day leaves NAV up to and including it unchanged
    b3 = {**b2, "510300": b2["510300"].assign(adj_close=lambda d: d.adj_close.where(d.Date <= sess[4], d.adj_close * 3),
                                              adj_open=lambda d: d.adj_open.where(d.Date <= sess[4], d.adj_open * 3))}
    nav3, _r, _m = run_backtest_cn(cal, b3, sess[0], sess[-1], fr, cfg, curve, CN, suspension_rules=True)
    k = list(nav["date"]).index(gap)
    assert nav["nav"].iloc[:k + 1].tolist() == nav3["nav"].iloc[:k + 1].tolist()


def test_rebalance_postponed_to_next_common_session_strictly_after_signal():
    bars, sess, curve, cfg = _amend_setup()
    cal = get_xshg_calendar()
    sched, sig = sess[3], sess[2]
    fr = _frame(sched, sig, {"510300": 0.5, "511010": 0.5})
    # target symbol 511010 suspended on scheduled execution and the next session; 510300 fine
    b2 = {**bars, "511010": bars["511010"][~bars["511010"].Date.isin([sess[3], sess[4]])].reset_index(drop=True)}
    nav, reb, m = run_backtest_cn(cal, b2, sess[0], sess[-1], fr, cfg, curve, CN, suspension_rules=True)
    assert len(m["postponements"]) == 1
    p = m["postponements"][0]
    assert p["scheduled_execution"] == sched and p["actual_execution"] == sess[5] and p["suspended_symbols"] == "511010"
    assert p["actual_execution"] > p["signal_session"]
    assert reb["execution_session"].tolist() == [sess[5]]      # executed once, with unchanged weights
    # before execution the portfolio is all cash; no trade on the scheduled day
    assert not nav.set_index("date").loc[sched, "rebalance_flag"] and nav.set_index("date").loc[sess[5], "rebalance_flag"]


def test_signals_use_only_real_closes_up_to_signal_close():
    bars, sess = _synthetic_cn_bars()
    cal = get_xshg_calendar()
    as_of = market_as_of_after_close_cal(cal, sess[-1], CN_TZ)
    weekly = build_weekly_schedule_cal(cal, sess, as_of)
    full = compute_model_signals_cn(bars, CN, sess, weekly, "W", 12, 40)
    row = full.rows[len(full.rows) // 2]
    cut = row["signal_session"]
    # a suspension (missing bar) in the lookback leaves that day NaN, never carried forward; future prices irrelevant
    altered = {s: b.assign(adj_close=b.adj_close.where(b.Date <= cut, b.adj_close * 5)) for s, b in bars.items()}
    alt = compute_model_signals_cn(altered, CN, sess, weekly, "W", 12, 40)
    same = [r for r in full.rows if r["signal_session"] <= cut]
    assert [r["momentum_scores"] for r in same] == [r["momentum_scores"] for r in alt.rows[:len(same)]]
    assert [r["trend_passes"] for r in same] == [r["trend_passes"] for r in alt.rows[:len(same)]]
