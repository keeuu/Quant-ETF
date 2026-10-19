"""us_long study: protocol hash guards, synthetic bond formula, calendar/engine adapter equivalence with the
research_v3 frozen-rule path, and CRASH_WEEKLY_EXIT mechanics."""
from __future__ import annotations

import math
import shutil

import numpy as np
import pandas as pd
import pytest

from research_v2.calendar import get_trading_calendar
from research_v3.cash_yield import CashYieldCurve, load_irx
from research_v3.config import V3_DEFAULT
from research_v3.engine import run_backtest_v3
from research_v3.protocol_step3 import load_verified as load_step3
from research_v3.step2 import _setup
from research_v3.step3 import variant_frames
from research_v3.us_long import data as D
from research_v3.us_long import protocols as PR
from research_v3.us_long.calendar_proxy import ProxyCalendar
from research_v3.us_long.engine_ul import execute_exit, run_backtest_ul
from research_v3.us_long.pipeline import build_challenger_rows, build_core
from research_v3.us_long.proxies import (EQUITY_GROUPS, GROUPS, UNIVERSE, par_bond_price, returns_to_index,
                                         synthetic_bond_returns)
from research_v3.us_long.run_primary import EQUITY
from research_v3.us_long.run_secondary import bars_to_prices

from research_v3.cn_transfer.calendar_cn import build_monthly_matched_schedule_cal, build_weekly_schedule_cal


# ------------------------------------------------------------------ protocol hash guards
def test_protocols_verify_and_refuse_on_mismatch(tmp_path):
    assert "universe_proxies" in PR.load_main()
    assert "rule" in PR.load_crash()
    for f in (*PR.MAIN, *PR.CRASH):
        shutil.copy(PR.HERE / f, tmp_path / f)
    PR.load_main(tmp_path)
    (tmp_path / PR.MAIN[0]).write_text((tmp_path / PR.MAIN[0]).read_text() + " ")
    with pytest.raises(PR.ProtocolChangedError):
        PR.load_main(tmp_path)
    (tmp_path / PR.CRASH[1]).write_text("0" * 64)
    with pytest.raises(PR.ProtocolChangedError):
        PR.load_crash(tmp_path)


def test_data_manifest_hash_detects_change(tmp_path):
    (tmp_path / "a.csv").write_text("x\n1\n")
    import json
    (tmp_path / "manifest.json").write_text(json.dumps({"files": {"a.csv": {"sha256": D.sha256_file(tmp_path / "a.csv")}}}))
    D.verify_manifest(tmp_path)
    (tmp_path / "a.csv").write_text("x\n2\n")
    with pytest.raises(D.USLongDataError):
        D.verify_manifest(tmp_path)


# ------------------------------------------------------------------ synthetic bond
def test_synthetic_bond_hand_computed():
    # y_{t-1}=4%, y_t=5%, M=10 -> n=20 semi-annual periods, i=2.5%, coupon/2=2%
    # hand: 1.025^-20 = 0.6102709, annuity = (1-0.6102709)/0.025 = 15.589164,
    #       P = 0.02*15.589164 + 0.6102709 = 0.9220541  -> price change -0.0779459
    #       r = 0.04/252 - 0.0779459 = -0.0777871
    assert par_bond_price(0.04, 0.05, 10) == pytest.approx(0.9220541, abs=2e-7)
    s = pd.Series([4.0, 5.0], index=["2000-01-03", "2000-01-04"])
    r = synthetic_bond_returns(s, 10)
    assert math.isnan(r.iloc[0])
    assert r.iloc[1] == pytest.approx(0.04 / 252 - 0.0779459, abs=2e-7)
    # independent brute-force cash-flow sum
    pv = sum(0.02 / (1.025 ** k) for k in range(1, 21)) + 1.0 / 1.025 ** 20
    assert par_bond_price(0.04, 0.05, 10) == pytest.approx(pv, abs=1e-12)


def test_par_bond_identities():
    assert par_bond_price(0.06, 0.06, 30) == pytest.approx(1.0, abs=1e-12)     # coupon == yield -> par
    assert par_bond_price(0.04, 0.0, 10) == pytest.approx(1.0 + 0.04 * 10)      # zero yield
    s = pd.Series([5.0, 5.0, 5.0], index=["2000-01-03", "2000-01-04", "2000-01-05"])
    assert synthetic_bond_returns(s, 10).iloc[1:].tolist() == pytest.approx([0.05 / 252] * 2)  # flat yield -> carry only


def test_bond_series_cut_at_gap_and_index():
    s = pd.Series([5.0, 5.0, 6.0, 6.0], index=["2000-01-03", "2000-01-04", "2000-02-20", "2000-02-21"])
    assert len(synthetic_bond_returns(s, 10)) == 2
    idx = returns_to_index(pd.Series([np.nan, 0.1, -0.1]))
    assert idx.tolist() == pytest.approx([100.0, 110.0, 99.0])


# ------------------------------------------------------------------ adapter equivalence (research_v2 US snapshot)
@pytest.fixture(scope="module", params=["train", "validation"])
def snap(request):
    view, sessions, weekly, monthly = _setup(request.param)
    return request.param, view, sessions, weekly, monthly, CashYieldCurve(load_irx())


def test_proxy_calendar_schedules_equal_xnys(snap):
    _, view, sessions, weekly, monthly, _ = snap
    cal = ProxyCalendar(sessions)
    from research_v3.us_long.calendar_proxy import as_of_after_close
    as_of = as_of_after_close(cal, sessions[-1])
    w2 = build_weekly_schedule_cal(cal, sessions, as_of)
    m2 = build_monthly_matched_schedule_cal(cal, sessions, as_of, w2)
    assert len(w2) == len(weekly) and len(m2) == len(monthly)
    for a, b in zip(w2, weekly):
        assert (a.signal_session, a.period_id, a.status) == (b.signal_session, b.period_id, b.status)
        if a.status == "COMPLETE":
            assert a.execution_session == b.execution_session
    for a, b in zip(m2, monthly):
        assert (a.signal_session, a.source_signal_session, a.status) == (b.signal_session, b.source_signal_session, b.status)
        if a.status == "COMPLETE":
            assert a.execution_session == b.execution_session


def test_adapter_open_execution_reproduces_research_v3_nav(snap):
    part, view, sessions, weekly, monthly, curve = snap
    p3 = load_step3()
    ref_frame = [f for n, c, f in variant_frames(view, sessions, weekly, monthly, 40, p3, curve)
                 if (n, c) == ("CAP_DEF", "M")][0]
    ref_nav, ref_reb, ref_m = run_backtest_v3(view, ref_frame, V3_DEFAULT, curve)

    cal = ProxyCalendar(sessions)
    core = build_core(cal, sessions, view.bars, curve)
    o, c = bars_to_prices(view.bars, UNIVERSE)               # open execution = real adj_open
    scored = [s for s in sessions if view.score_start <= s <= view.score_end]
    nav, reb, m = run_backtest_ul(get_trading_calendar(), scored, core.capdef.rows, o, c, V3_DEFAULT, curve,
                                  UNIVERSE, "CAP_DEF_M")
    assert len(nav) == len(ref_nav)
    np.testing.assert_allclose(nav["nav"].to_numpy(), ref_nav["nav"].to_numpy(), rtol=1e-12, atol=0)
    assert nav["date"].tolist() == ref_nav["date"].tolist()
    assert len(reb) == len(ref_reb)
    assert m["max_drawdown"] == pytest.approx(ref_m["max_drawdown"], abs=1e-12)
    assert m["annualized_one_way_turnover"] == pytest.approx(ref_m["annualized_one_way_turnover"], abs=1e-12)
    # signal rows are identical to the research_v3 frame too
    ref_rows = {r["execution_session"]: r["weights"] for r in ref_frame.rows if r["status"] == "COMPLETE"}
    for r in core.capdef.rows:
        if r["status"] == "COMPLETE" and r["execution_session"] in ref_rows:
            assert r["weights"] == ref_rows[r["execution_session"]]


def test_close_execution_trades_at_close_of_execution_session():
    cal = ProxyCalendar(["2000-01-03", "2000-01-04", "2000-01-05"])
    px = {s: {"2000-01-03": 100.0, "2000-01-04": 110.0, "2000-01-05": 121.0} for s in UNIVERSE}
    cfg = V3_DEFAULT.__class__(cost_bps=0.0, slippage_bps=0.0, cash_yield_source="none")
    w = {s: 0.0 for s in UNIVERSE}
    w["SPY"] = 1.0
    w["CASH"] = 0.0
    rows = [{"signal_session": "2000-01-03", "execution_session": "2000-01-04", "status": "COMPLETE", "weights": w}]
    nav, _r, _m = run_backtest_ul(cal, ["2000-01-03", "2000-01-04", "2000-01-05"], rows, px, px, cfg, None, UNIVERSE, "t")
    # bought at the 01-04 close (110): no gain on 01-04, +10% on 01-05
    assert nav["nav"].tolist() == pytest.approx([100000.0, 100000.0, 110000.0])


# ------------------------------------------------------------------ CRASH_WEEKLY_EXIT mechanics (pure logic)
def _wrow(sess, exec_, passes_false=(), status="COMPLETE"):
    tp = {s: True for s in UNIVERSE}
    for s in passes_false:
        tp[s] = False
    return {"signal_session": sess, "execution_session": exec_, "status": status, "trend_passes": tp}


def _mrow(sig, exec_, weights):
    w = {s: 0.0 for s in UNIVERSE}
    w["CASH"] = 0.0
    w.update(weights)
    return {"signal_session": sig, "execution_session": exec_, "status": "COMPLETE", "weights": w}


MONTHLY = [_mrow("2020-01-31", "2020-02-03", {"SPY": 1 / 3, "EFA": 1 / 3, "GLD": 1 / 3}),
           _mrow("2020-02-28", "2020-03-02", {"IWM": 1 / 3, "GLD": 1 / 3, "IEF": 1 / 3})]


def _build(weekly, defensive=lambda s: "IEF", check=True, monthly=MONTHLY, msig=("2020-01-31", "2020-02-28")):
    return build_challenger_rows(monthly, weekly, set(msig), defensive, equity_symbols=EQUITY, def_symbol="IEF",
                                 score_start="2020-02-03", score_end="2020-12-31", weekly_check=check)


def test_weekly_exit_touches_only_equity_holdings():
    weekly = [_wrow("2020-02-07", "2020-02-10", passes_false=("GLD", "IEF", "TLT", "QQQ"))]   # non-held equity + non-equity fail
    rows, log = _build(weekly)
    assert log == [] and [r["kind"] for r in rows] == ["full", "full"]                          # nothing held fails -> no exit
    weekly = [_wrow("2020-02-07", "2020-02-10", passes_false=("GLD", "SPY"))]
    rows, log = _build(weekly)
    ex = [r for r in rows if r["kind"] == "exit"]
    assert len(ex) == 1 and ex[0]["exits"] == {"SPY": "IEF"}                                    # GLD (gold) untouched, EFA still passes
    assert ex[0]["weights"]["GLD"] == pytest.approx(1 / 3) and ex[0]["weights"]["EFA"] == pytest.approx(1 / 3)


def test_weekly_exit_moves_weight_to_def_choice():
    weekly = [_wrow("2020-02-07", "2020-02-10", passes_false=("SPY",))]
    rows, _ = _build(weekly, defensive=lambda s: "IEF")
    ex = [r for r in rows if r["kind"] == "exit"][0]
    assert ex["exits"] == {"SPY": "IEF"} and ex["weights"]["IEF"] == pytest.approx(1 / 3) and ex["weights"].get("SPY", 0.0) == 0.0
    rows, _ = _build(weekly, defensive=lambda s: "CASH")
    ex = [r for r in rows if r["kind"] == "exit"][0]
    assert ex["exits"] == {"SPY": "CASH"} and ex["weights"]["CASH"] == pytest.approx(1 / 3) and ex["weights"].get("IEF", 0.0) == 0.0


def test_no_reentry_until_next_monthly_signal():
    weekly = [_wrow("2020-02-07", "2020-02-10", passes_false=("SPY",)),
              _wrow("2020-02-14", "2020-02-18"),                                  # SPY passes again: no re-entry
              _wrow("2020-02-21", "2020-02-24", passes_false=("EFA",)),           # a second asset fails: cumulative exit
              _wrow("2020-03-06", "2020-03-09", passes_false=("IWM",))]           # after the new monthly row: IWM held now
    rows, log = _build(weekly)
    ex = [r for r in rows if r["kind"] == "exit"]
    assert [r["signal_session"] for r in ex] == ["2020-02-07", "2020-02-21", "2020-03-06"]
    assert ex[1]["exits"] == {"SPY": "IEF", "EFA": "IEF"}
    assert ex[1]["weights"].get("SPY", 0.0) == 0.0 and ex[1]["weights"]["IEF"] == pytest.approx(2 / 3)
    assert ex[2]["exits"] == {"IWM": "IEF"}            # state restarted from the frozen monthly target (cumulative reset)
    # no row ever raises an equity weight after an exit within the month
    assert all(ex[1]["weights"].get(s, 0.0) <= ex[0]["weights"].get(s, 0.0) + 1e-12 for s in EQUITY)


def test_weekly_signal_on_monthly_signal_session_is_skipped():
    weekly = [_wrow("2020-02-28", "2020-03-02", passes_false=("SPY", "EFA"))]
    rows, log = _build(weekly, msig=("2020-01-31", "2020-02-28"))
    assert log == []


def test_disabled_weekly_check_returns_frozen_rows_only():
    weekly = [_wrow("2020-02-07", "2020-02-10", passes_false=("SPY",))]
    rows, log = _build(weekly, check=False)
    assert log == [] and [r["execution_session"] for r in rows] == ["2020-02-03", "2020-03-02"]
    assert all(r["weights"] == m["weights"] for r, m in zip(rows, MONTHLY))


# ------------------------------------------------------------------ exit execution (engine)
def test_execute_exit_conserves_value_and_leaves_other_holdings_alone():
    sh = {s: 0.0 for s in UNIVERSE}
    sh.update(SPY=10.0, GLD=5.0, TLT=2.0)
    px = {s: {"d": 100.0} for s in UNIVERSE}
    px["GLD"]["d"] = 200.0
    new, cash, rec, sold = execute_exit({"signal_session": "x", "exits": {"SPY": "IEF"}}, "d", sh, 50.0, px, 10.0,
                                        UNIVERSE, "m")
    assert sold == ["SPY"] and new["SPY"] == 0.0 and new["GLD"] == 5.0 and new["TLT"] == 2.0 and cash == 50.0
    c = 10.0 / 1e4
    assert new["IEF"] * 100.0 == pytest.approx(1000.0 * (1 - c) / (1 + c))
    assert rec["V_post"] == pytest.approx(rec["V_pre"] - rec["fee"])
    assert rec["fee"] == pytest.approx(c * (1000.0 + new["IEF"] * 100.0))
    new, cash, rec, sold = execute_exit({"signal_session": "x", "exits": {"SPY": "CASH"}}, "d", sh, 50.0, px, 10.0,
                                        UNIVERSE, "m")
    assert cash == pytest.approx(50.0 + 1000.0 * (1 - c)) and new["IEF"] == 0.0 and new["GLD"] == 5.0


# ------------------------------------------------------------------ integration on the real US snapshot (validation)
@pytest.fixture(scope="module")
def integ():
    view, sessions, weekly, monthly = _setup("validation")
    curve = CashYieldCurve(load_irx())
    core = build_core(ProxyCalendar(sessions), sessions, view.bars, curve)
    o, c = bars_to_prices(view.bars, UNIVERSE)
    scored = [s for s in sessions if view.score_start <= s <= view.score_end]
    msig = {m.signal_session for m in monthly}
    kw = dict(equity_symbols=EQUITY, def_symbol="IEF", score_start=view.score_start, score_end=view.score_end)
    return dict(view=view, core=core, o=o, c=c, scored=scored, curve=curve, msig=msig, kw=kw)


def _run(integ, rows):
    return run_backtest_ul(get_trading_calendar(), integ["scored"], rows, integ["o"], integ["c"], V3_DEFAULT,
                           integ["curve"], UNIVERSE, "x", record_positions=True)


def test_disabled_check_reproduces_frozen_exactly_and_enabled_exits_behave(integ):
    core = integ["core"]
    frozen_nav, frozen_reb, _ = _run(integ, [r for r in core.capdef.rows])
    off_rows, off_log = build_challenger_rows(core.capdef.rows, core.wf.rows, integ["msig"], core.defensive,
                                              weekly_check=False, **integ["kw"])
    off_nav, _r, _m = _run(integ, off_rows)
    assert off_log == [] and off_nav["nav"].equals(frozen_nav["nav"])                   # bit-identical
    rows, log = build_challenger_rows(core.capdef.rows, core.wf.rows, integ["msig"], core.defensive, **integ["kw"])
    assert len(log) > 0
    nav, reb, m = _run(integ, rows)
    pos = m["positions"].set_index("date")
    dates = list(pos.index)
    equity_syms = [s for s in UNIVERSE if s in EQUITY]
    non_eq = [s for s in UNIVERSE if s not in EQUITY]
    full_exec = {r["execution_session"] for r in core.capdef.rows}
    for e in m["exit_log"]:
        d = e["execution_session"]
        i = dates.index(d)
        prev, cur = pos.iloc[i - 1], pos.iloc[i]
        sold = e["sold"].split(",")
        for s in sold:
            assert prev[s] > 0 and cur[s] == 0.0                                       # exited holding is sold
            assert s in EQUITY                                                         # only equity groups
        for s in non_eq:
            if not (s == "IEF" and e["dest"] == "IEF"):
                assert cur[s] == prev[s]                                               # gold / bonds untouched
        for s in equity_syms:
            if s not in sold:
                assert cur[s] == prev[s]                                               # other equity untouched
        if e["dest"] == "IEF":
            assert cur["IEF"] > prev["IEF"]
        else:
            assert cur["cash"] > prev["cash"]
    # no re-entry: between an exit and the next frozen execution the exited symbol is never bought back
    exits = m["exit_log"]
    nxt_full = sorted(full_exec)
    for e in exits:
        d = e["execution_session"]
        later = [x for x in nxt_full if x > d]
        end = later[0] if later else dates[-1]
        for s in e["sold"].split(","):
            seg = pos[(pos.index >= d) & (pos.index < end)][s]
            assert (seg == 0.0).all()
