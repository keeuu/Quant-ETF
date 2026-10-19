"""Tests for research_v3.sector_mom (development phase): protocol hash, holdout guard, T+1, price limits with retry,
MA exit / re-entry modes, point-in-time eligibility + dedup, inverse-vol weights with cash slots, classification."""
import json

import numpy as np
import pandas as pd
import pytest

from research_v3.sector_mom import guard
from research_v3.sector_mom.data import DataError, load_bars_for_scoring, load_shibor_for_scoring, verify_manifest, write_manifest
from research_v3.sector_mom.engine import PRIMARY, ZERO, Cost, Plan, StratSpec, simulate
from research_v3.sector_mom.panel import MIN_AGE, MIN_TURN60, Panel
from research_v3.sector_mom.protocol import ProtocolChangedError, load_verified
from research_v3.sector_mom import universe as U


class FlatCurve:
    def accrual_factor(self, a, b):
        return 1.0


def sessions_from(start, n):
    return [d.strftime("%Y-%m-%d") for d in pd.bdate_range(start, periods=n)]


def mk_bars(sessions, opens, closes, turnover=1e9):
    n = len(sessions)
    to = np.full(n, turnover) if np.isscalar(turnover) else np.asarray(turnover, float)
    df = pd.DataFrame({"Date": sessions, "open": opens, "high": np.maximum(opens, closes), "low": np.minimum(opens, closes),
                       "close": closes, "volume": 1.0, "turnover": to})
    return df.dropna(subset=["close"]).reset_index(drop=True)


def toy_panel(series: dict, sessions, keys=None, limits=None, sector=None, turnover=None):
    bars = {t: mk_bars(sessions, np.asarray(o, float), np.asarray(c, float), (turnover or {}).get(t, 1e9))
            for t, (o, c) in series.items()}
    keys = keys or {t: f"IDX_{t}" for t in series}
    return Panel(bars, sessions, keys, limits or {t: 0.10 for t in series}, set(sector if sector is not None else series))


# ------------------------------------------------------------------------------------------------ protocol hash
def test_protocol_hash_ok_and_rejection(tmp_path):
    p = load_verified()
    assert "S1_WEEKLY_MOM" in p["strategies"]
    proto = tmp_path / "p.json"
    proto.write_text('{"a": 1}')
    h = tmp_path / "p.sha256"
    h.write_text("0" * 64)
    with pytest.raises(ProtocolChangedError):
        load_verified(proto, h)
    import hashlib
    h.write_text(hashlib.sha256(proto.read_bytes()).hexdigest())
    assert load_verified(proto, h) == {"a": 1}
    proto.write_text('{"a": 2}')                     # edited after hashing
    with pytest.raises(ProtocolChangedError):
        load_verified(proto, h)


# ------------------------------------------------------------------------------------------------ holdout guard
def test_guard_dates():
    guard.assert_date_allowed("2024-09-30")
    with pytest.raises(guard.HoldoutAccessError):
        guard.assert_date_allowed("2024-10-01")
    with pytest.raises(guard.HoldoutAccessError):
        guard.assert_dates_allowed(["2024-09-30", "2025-03-03"])


def _write_snapshot(d, rows_dates):
    (d / "bars").mkdir(parents=True)
    df = pd.DataFrame({"Date": rows_dates, "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1.0, "turnover": 1e8})
    df.to_csv(d / "bars" / "AAA.csv", index=False)
    pd.DataFrame({"Date": rows_dates, "shibor_3m_pct": 2.0}).to_csv(d / "shibor_3m.csv", index=False)
    write_manifest(d)


def test_loader_clips_holdout_and_refuses_explicit_request(tmp_path):
    _write_snapshot(tmp_path, ["2024-09-27", "2024-09-30", "2024-10-08", "2025-01-02"])
    b = load_bars_for_scoring(["AAA"], tmp_path)
    assert b["AAA"]["Date"].max() == "2024-09-30"                       # stored later rows never reach scoring
    assert load_shibor_for_scoring(tmp_path).index.max() == "2024-09-30"
    with pytest.raises(guard.HoldoutAccessError):
        load_bars_for_scoring(["AAA"], tmp_path, through="2024-10-08")
    with pytest.raises(guard.HoldoutAccessError):
        load_shibor_for_scoring(tmp_path, through="2025-01-02")
    (tmp_path / "bars" / "AAA.csv").write_text("tampered")              # manifest hash check
    with pytest.raises(DataError):
        verify_manifest(tmp_path)


def test_panel_and_engine_refuse_holdout_sessions():
    s = sessions_from("2024-09-20", 20)                                    # runs into October 2024
    assert s[-1] > guard.DEV_END
    with pytest.raises(guard.HoldoutAccessError):
        toy_panel({"X": (np.ones(20), np.ones(20))}, s)
    s2 = sessions_from("2024-01-02", 40)
    p = toy_panel({"X": (np.ones(40), np.ones(40))}, s2)
    p.sessions = s[:]                                                      # simulate a panel reaching the holdout
    with pytest.raises(guard.HoldoutAccessError):
        simulate(p, Plan(weekly=[(0, 1, {"X": 1.0})]), StratSpec("S", "mom"), ZERO, 1e6, False, FlatCurve(), 1, 19)


# ------------------------------------------------------------------------------------------------ T+1
def test_t_plus_1_no_same_day_sell():
    s = sessions_from("2020-01-02", 30)
    c = np.array([10.0] * 5 + [10.2, 10.4, 10.6, 10.8, 11.0] + [11.0] * 5 + [9.0] * 15)   # drops below MA10 late
    o = c.copy()
    pan = toy_panel({"X": (o, c)}, s)
    # buy at open of session 1; deselect signal at close of session 1 -> sale at open of session 2 (earliest allowed)
    plan = Plan(weekly=[(0, 1, {"X": 1.0}), (1, 2, {})])
    r = simulate(pan, plan, StratSpec("S1", "mom"), ZERO, 1e6, False, FlatCurve(), 1, 29)
    tr = pd.DataFrame(r.trades)
    assert list(tr.side) == ["BUY", "SELL"] and tr.date.tolist() == [s[1], s[2]]
    assert r.counters["t1_blocked_sell"] == 0
    for _, g in tr.groupby("ticker"):                                     # never BUY and SELL on one session
        assert not (set(g[g.side == "BUY"].date) & set(g[g.side == "SELL"].date))


def test_ma_exit_triggered_on_buy_day_sells_next_open_not_same_day():
    s = sessions_from("2020-01-02", 30)
    c = np.array([10.0] * 12 + [8.0] * 18)                                # close of day 12 collapses below SMA5
    o = np.r_[c[:12], 10.0, c[13:]]                                      # day 12 open still 10
    pan = toy_panel({"X": (o, c)}, s)
    plan = Plan(weekly=[(10, 11, {"X": 1.0})])
    r = simulate(pan, plan, StratSpec("S3", "mom", exit_ma=5), ZERO, 1e6, False, FlatCurve(), 11, 29)
    tr = pd.DataFrame(r.trades)
    assert tr[tr.side == "BUY"].date.iloc[0] == s[11]
    sell = tr[tr.side == "SELL"].iloc[0]
    assert sell.date == s[13] and sell.reason.startswith("ma5_exit")       # decided at close of 12 -> open of 13


# ------------------------------------------------------------------------------------------------ price limits
def test_limit_blocked_buy_and_sell_retry():
    s = sessions_from("2020-01-02", 12)
    c = np.array([10.0, 10.0, 10.0, 10.0, 10.0, 10.0, 10.0, 10.0, 9.0, 9.0, 9.0, 9.0])
    o = np.array([10.0, 11.0, 10.5, 10.5, 10.0, 10.0, 10.0, 10.0, 8.0, 9.0, 9.0, 9.0])
    # day1 open 11.0 = prev close 10 * 1.10 -> buy blocked (limit-up); day2 open 10.5 -> filled.
    # sell signal at close of day 6 -> day7 open 10.0 fine?  use explicit: deselect at close of day 7 -> day8 open 8.0 (prev close 10 -> -20%: blocked)
    pan = toy_panel({"X": (o, c)}, s)
    plan = Plan(weekly=[(0, 1, {"X": 1.0}), (7, 8, {})])
    r = simulate(pan, plan, StratSpec("S1", "mom"), ZERO, 1e6, False, FlatCurve(), 1, 11)
    tr = pd.DataFrame(r.trades)
    b = tr[tr.side == "BUY"]
    assert len(b) == 1 and b.date.iloc[0] == s[2] and abs(b.price.iloc[0] - 10.5) < 1e-12
    assert r.counters["limit_blocked_buy"] == 1
    sl = tr[tr.side == "SELL"]
    # day 8 open 8.0 <= 10*0.9 -> blocked; day 9 open 9.0 vs prev close 9.0 -> sold
    assert len(sl) == 1 and sl.date.iloc[0] == s[9]
    assert r.counters["limit_blocked_sell"] == 1
    assert abs(r.final_shares.get("X", 0.0)) < 1e-9


def test_twenty_percent_board_not_blocked_at_ten_percent():
    s = sessions_from("2020-01-02", 6)
    c = np.full(6, 10.0)
    o = np.array([10.0, 11.5, 10.0, 10.0, 10.0, 10.0])
    pan = toy_panel({"X": (o, c)}, s, limits={"X": 0.20})
    r = simulate(pan, Plan(weekly=[(0, 1, {"X": 1.0})]), StratSpec("S1", "mom"), ZERO, 1e6, False, FlatCurve(), 1, 5)
    assert r.counters["limit_blocked_buy"] == 0 and pd.DataFrame(r.trades).date.iloc[0] == s[1]


# ------------------------------------------------------------------------------------------------ MA exit / re-entry
def _ma_series():
    # 20 sessions rising slowly, a dip (days 14-15), recovery above SMA10 on day 17, weekly signals every 5 sessions
    c = np.array([10 + 0.1 * k for k in range(14)] + [10.5, 10.0, 10.9, 11.6, 11.7, 11.8, 11.9, 12.0, 12.1, 12.2, 12.3, 12.4, 12.5, 12.6, 12.7, 12.8])
    return c


def test_ma10_exit_weekly_vs_daily_reentry():
    c = _ma_series()
    n = len(c)
    s = sessions_from("2020-01-02", n)
    pan = toy_panel({"X": (c.copy(), c)}, s)
    sma10 = pd.Series(c).rolling(10).mean().to_numpy()
    below = [k for k in range(10, n) if c[k] < sma10[k]]
    above_after = [k for k in range(below[0] + 1, n) if c[k] > sma10[k]]
    k_exit, k_back = below[0], above_after[0]
    assert k_back > k_exit
    # weekly events: signal at close of 10, 15, 20, 25 ... (exec next session), X always selected with weight 1
    wk = [(k, k + 1, {"X": 1.0}) for k in range(10, n - 1, 5)]
    plan = Plan(weekly=wk)
    s2 = simulate(pan, plan, StratSpec("S2", "mom", exit_ma=10), ZERO, 1e6, False, FlatCurve(), 11, n - 1)
    s4 = simulate(pan, plan, StratSpec("S4", "mom", exit_ma=10, daily_reentry=True), ZERO, 1e6, False, FlatCurve(), 11, n - 1)
    t2, t4 = pd.DataFrame(s2.trades), pd.DataFrame(s4.trades)
    # both exit at the open after the first close below SMA10 (unless that close is itself a weekly signal day: then exit via weekly rule)
    sell2, sell4 = t2[t2.side == "SELL"].iloc[0], t4[t4.side == "SELL"].iloc[0]
    assert sell2.date == s[k_exit + 1] and sell4.date == s[k_exit + 1]
    # S4 re-enters at the open after the first close back above SMA10 (daily); S2 waits for the next weekly rebalance
    buy4 = t4[(t4.side == "BUY") & (t4.date > sell4.date)].iloc[0]
    assert buy4.date == s[k_back + 1] and buy4.reason == "daily_reentry"
    next_weekly_exec = min(ep for sp, ep, _ in wk if sp >= k_exit + 1 and sp >= k_back - 0 and c[sp] > sma10[sp])
    buy2 = t2[(t2.side == "BUY") & (t2.date > sell2.date)].iloc[0]
    assert buy2.date == s[next_weekly_exec] and buy2.reason == "weekly_rebalance"
    assert buy4.date < buy2.date                                           # daily re-entry strictly earlier here


def test_weekly_reentry_requires_close_above_ma_at_signal():
    # price stays below SMA10 at the weekly signal after the exit -> S2 must not re-buy at that rebalance
    c = np.array([10 + 0.2 * k for k in range(12)] + [11.0] * 3 + [9.0 - 0.05 * k for k in range(20)])
    n = len(c)
    s = sessions_from("2020-01-02", n)
    pan = toy_panel({"X": (c.copy(), c)}, s)
    wk = [(k, k + 1, {"X": 1.0}) for k in range(10, n - 1, 5)]
    r = simulate(pan, Plan(weekly=wk), StratSpec("S2", "mom", exit_ma=10), ZERO, 1e6, False, FlatCurve(), 11, n - 1)
    t = pd.DataFrame(r.trades)
    assert (t.side == "BUY").sum() == 1 and (t.side == "SELL").sum() == 1
    assert abs(r.final_shares.get("X", 0.0)) < 1e-9


# ------------------------------------------------------------------------------------------------ eligibility / dedup
def test_eligibility_age_turnover_bar_and_dedup():
    n = MIN_AGE + 40
    s = sessions_from("2015-01-05", n)
    rng = np.random.default_rng(1)
    def walk(vol): return 10 * np.exp(np.cumsum(rng.normal(0, vol, n)))
    series, turn = {}, {}
    for t, vol in [("A", .01), ("B", .012), ("C", .011), ("D", .013), ("E", .01), ("F", .01), ("G", .01)]:
        c = walk(vol)
        series[t] = (c.copy(), c.copy())
    # B listed late (age < 250), D thin turnover, G has no bar on the signal date, E/F same index (E more liquid)
    series["B"][0][:100] = np.nan; series["B"][1][:100] = np.nan
    series["G"][0][-1] = np.nan; series["G"][1][-1] = np.nan
    turn = {"A": 5e7, "B": 5e7, "C": 5e7, "D": 1e7, "E": 9e7, "F": 4e7, "G": 5e7}
    keys = {t: f"IDX_{t}" for t in series}
    keys["F"] = keys["E"]
    pan = toy_panel(series, s, keys=keys, turnover=turn)
    i = n - 1
    pre = [pan.tickers[j] for j in pan.eligible_pre_dedup(i)]
    assert pre == ["A", "C", "E", "F"]                                    # B young, D thin, G no bar
    post = [pan.tickers[j] for j in pan.eligible_dedup(i)]
    assert post == ["A", "C", "E"]                                        # F dropped: same index as E with lower turnover
    assert pan.age[i, pan.col["B"]] < MIN_AGE and pan.turn60[i, pan.col["D"]] < MIN_TURN60
    # exactly at the age threshold: eligible on the 250th session, not on the 249th
    j = pan.col["A"]
    assert pan.age[MIN_AGE - 1, j] == MIN_AGE and pan.age[MIN_AGE - 2, j] == MIN_AGE - 1


# ------------------------------------------------------------------------------------------------ weights / cash slots
def test_inverse_vol_weights_and_unfilled_slots_to_cash():
    n = 80
    s = sessions_from("2020-01-02", n)
    rng = np.random.default_rng(3)
    series = {}
    for t, vol, drift in [("A", .01, .004), ("B", .02, .004), ("C", .04, .004)]:
        c = 10 * np.exp(np.cumsum(rng.normal(drift, vol, n)))
        series[t] = (c.copy(), c.copy())
    pan = toy_panel(series, s)
    i = n - 1
    elig = [pan.col[t] for t in "ABC"]
    tg = pan.s1_target(i, elig)
    cand = [t for t in "ABC" if pan.CLf[i, pan.col[t]] > pan.sma[20][i, pan.col[t]]]
    assert set(tg) == set(cand) and len(tg) >= 1
    k = len(tg)
    assert abs(sum(tg.values()) - k / 5.0) < 1e-12                       # unfilled slots stay cash: sum = k/5
    inv = {t: 1.0 / pan.sigma20[i, pan.col[t]] for t in tg}
    for t in tg:
        assert abs(tg[t] - (k / 5.0) * inv[t] / sum(inv.values())) < 1e-12
    # engine: zero cost, flat prices -> cash weight after the buy equals the unfilled share
    s2 = sessions_from("2020-01-02", 6)
    flat = {t: (np.full(6, 10.0), np.full(6, 10.0)) for t in "ABC"}
    pan2 = toy_panel(flat, s2)
    r = simulate(pan2, Plan(weekly=[(0, 1, {"A": 0.3, "B": 0.2, "C": 0.1})]), StratSpec("S1", "mom"), ZERO, 1e6, False, FlatCurve(), 1, 5)
    assert abs(r.nav.cash.iloc[-1] / r.nav.nav.iloc[-1] - 0.4) < 1e-9


def test_five_selected_weights_sum_to_one():
    n = 80
    s = sessions_from("2020-01-02", n)
    rng = np.random.default_rng(5)
    series = {}
    for k, t in enumerate("ABCDEFG"):
        c = 10 * np.exp(np.cumsum(rng.normal(0.005, 0.01 + 0.003 * k, n)))
        series[t] = (c.copy(), c.copy())
    pan = toy_panel(series, s)
    tg = pan.s1_target(n - 1, list(range(7)))
    if len(tg) == 5:
        assert abs(sum(tg.values()) - 1.0) < 1e-12


# ------------------------------------------------------------------------------------------------ costs
def test_cost_charged_per_side_and_lot_minimum_commission():
    s = sessions_from("2020-01-02", 6)
    pan = toy_panel({"X": (np.full(6, 10.0), np.full(6, 10.0))}, s)
    r = simulate(pan, Plan(weekly=[(0, 1, {"X": 1.0})]), StratSpec("S1", "mom"), PRIMARY, 1e6, False, FlatCurve(), 1, 5)
    t = r.trades[0]
    assert abs(t["fee"] - t["notional"] * 12.5e-4) < 1e-6
    assert abs(r.nav.nav.iloc[-1] - 1e6 * (1 - 12.5e-4 / (1 + 12.5e-4))) < 1.0
    r2 = simulate(pan, Plan(weekly=[(0, 1, {"X": 0.05})]), StratSpec("S1", "mom"), PRIMARY, 100_000.0, True, FlatCurve(), 1, 5)
    t2 = r2.trades[0]
    assert t2["shares"] % 100 == 0 and t2["fee"] >= 5.0 + t2["notional"] * 10e-4 - 1e-9


# ------------------------------------------------------------------------------------------------ classification
@pytest.mark.parametrize("idx,name,ftype,included", [
    ("中证全指半导体产品与设备指数", "半导体ETF", "指数型-股票", True),
    ("中证银行指数", "银行ETF", "指数型-股票", True),
    ("沪深300指数", "300ETF", "指数型-股票", False),
    ("中证红利指数", "红利ETF", "指数型-股票", False),
    ("恒生科技指数", "恒生科技ETF", "指数型-股票", False),
    ("上证国债指数", "国债ETF", "指数型-固收", False),
    ("创业板指数", "创业板ETF", "指数型-股票", False),
    ("创业板新能源指数", "创业板新能源ETF", "指数型-股票", True),
    ("国证价值100指数", "价值ETF", "指数型-股票", False),
])
def test_classification_examples(idx, name, ftype, included):
    rec = {"ticker": "999999", "thscode": "999999.SH", "exchange": "SH", "name": name}
    out = U.classify_row(rec, {"跟踪标的": idx, "基金类型": ftype, "业绩比较基准": ""})
    assert out["included"] is included
    if "创业板" in idx or "科创" in idx:
        assert out["price_limit"] == 0.20


# ------------------------------------------------------------------------------------------------ real snapshot (skipped if absent)
def test_real_snapshot_never_reaches_scoring_beyond_dev_end():
    from research_v3.sector_mom.data import DATA_DIR
    if not (DATA_DIR / "manifest.json").exists():
        pytest.skip("no snapshot")
    verify_manifest(DATA_DIR)
    b = load_bars_for_scoring(["510300"], DATA_DIR, verify=False)["510300"]
    assert b["Date"].max() <= guard.DEV_END
    raw = pd.read_csv(DATA_DIR / "bars" / "510300.csv", dtype={"Date": str})
    assert raw["Date"].max() > guard.DEV_END                              # stored, but never loaded for scoring
    assert load_shibor_for_scoring(DATA_DIR, verify=False).index.max() <= guard.DEV_END


def test_classified_universe_hash_and_every_row_has_reason():
    df = pd.read_csv(U.OUT_CSV, dtype={"ticker": str})
    assert U.load_included().shape[0] == int(df.included.sum())
    assert df.reason.notna().all() and df.ticker.is_unique


# ------------------------------------------------------------------------------------------------ amendment 1 (S5-S7)
def test_amendment_hash_rejection(tmp_path):
    from research_v3.sector_mom.protocol_amend import load_amendment_verified
    assert load_amendment_verified()["amendment"] == 1
    p = tmp_path / "a.json"
    p.write_text('{"amendment": 1}')
    h = tmp_path / "a.sha256"
    h.write_text("f" * 64)
    with pytest.raises(ProtocolChangedError):
        load_amendment_verified(p, h)


def _ramp_panel(n=300):
    s = sessions_from("2019-01-02", n)
    c = 10 * (1.001 ** np.arange(n)) + 0.01 * np.arange(n) ** 1.2
    return toy_panel({"X": (c.copy(), c)}, s), c


def test_skip_momentum_windows_no_lookahead():
    pan, c = _ramp_panel()
    i = 280
    assert abs(pan.skip_mom(i, 252)[0] - (c[i - 21] / c[i - 252] - 1)) < 1e-12
    assert abs(pan.skip_mom(i, 126)[0] - (c[i - 21] / c[i - 126] - 1)) < 1e-12
    c2 = c.copy(); c2[i - 20:] *= 5.0                                      # changing the last 21 sessions must not matter
    pan2 = toy_panel({"X": (c2.copy(), c2)}, sessions_from("2019-01-02", 300))
    assert pan2.skip_mom(i, 252)[0] == pan.skip_mom(i, 252)[0]
    assert np.isnan(pan.skip_mom(100, 252)[0])


def test_dual_momentum_cash_hurdle_filter():
    from research_v3.sector_mom.run_amend1 import POOL  # noqa: F401  (module imports cleanly)
    n = 300
    s = sessions_from("2019-01-02", n)
    up = 10 * 1.0008 ** np.arange(n)
    flat = np.full(n, 10.0)
    pan = toy_panel({"UP": (up.copy(), up), "FLAT": (flat.copy(), flat)}, s)

    class Curve:  # 10% annual cash: hurdle over 231 sessions ~ 9%+, UP returns ~ 0.0008*231=20%
        def accrual_factor(self, a, b):
            return 1 + 0.10 * (pd.Timestamp(b) - pd.Timestamp(a)).days / 365
    el = [0, 1]
    tg = pan.amend_target(280, el, 252, Curve(), dual=True)
    assert set(tg) == {"UP"} and tg["UP"] == 0.2                           # FLAT (0%) < cash hurdle -> slot stays cash
    tg2 = pan.amend_target(280, el, 252, Curve(), dual=False)
    assert set(tg2) == {"UP", "FLAT"} and all(v == 0.2 for v in tg2.values())
    class Hi:
        def accrual_factor(self, a, b):
            return 1 + 1.0 * (pd.Timestamp(b) - pd.Timestamp(a)).days / 365
    assert pan.amend_target(280, el, 252, Hi(), dual=True) == {}          # hurdle above everything -> all cash


def test_monthly_signal_dates_last_session_of_month_exec_next_open():
    from research_v3.sector_mom.run_amend1 import month_end_events
    s = sessions_from("2019-01-02", 120)
    pan = toy_panel({"X": (np.ones(120), np.ones(120))}, s)
    ev = month_end_events(pan, 0)
    assert ev
    for sp, ep in ev:
        assert ep == sp + 1 and s[sp][:7] != s[ep][:7]
        assert s[sp][:7] == pd.Timestamp(s[sp]).strftime("%Y-%m")
    months = {s[sp][:7] for sp, _ in ev}
    assert "2019-01" in months and len(months) == len(ev)


# ------------------------------------------------------------------------------------------------ HYBRID
def test_hybrid_protocol_hash_rejection(tmp_path):
    from research_v3.sector_mom.protocol_hybrid_loader import load_hybrid_verified
    assert "hybrid_rule" in load_hybrid_verified()
    p = tmp_path / "h.json"; p.write_text("{}")
    h = tmp_path / "h.sha256"; h.write_text("1" * 64)
    with pytest.raises(ProtocolChangedError):
        load_hybrid_verified(p, h)


@pytest.mark.parametrize("E", [0.0, 1 / 3, 2 / 3])
def test_hybrid_weights_sum_and_non_equity_unchanged(E):
    from research_v3.sector_mom.hybrid import hybrid_weights
    cap = {"510300": E, "518880": 1 / 3, "511010": 1 / 3 - 0.0, "CASH": 1 / 3 - E + 0.0}
    cap = {"510300": E, "518880": 1 / 3, "511010": 1 / 3, "CASH": 1 - E - 2 / 3 if E < 1 / 3 + 1e-9 else 0.0}
    # build consistent cap weights: equity E, gold 1/3, bond 1/3, rest cash
    cap = {"510300": E, "518880": 1 / 3, "511010": 1 / 3}
    cap["CASH"] = 1 - sum(cap.values()) if sum(cap.values()) < 1 else 0.0
    if E > 1 / 3 + 1e-9:                                     # E = 2/3 leaves no room for gold + bond: use gold only
        cap = {"510300": E, "518880": 1 / 3, "CASH": 0.0}
    sec = ["A", "B", "C", "D", "E", "F"]
    w = hybrid_weights(cap, sec)
    assert abs(sum(w.values()) + cap["CASH"] - 1.0) < 1e-12
    for t in ("518880", "511010"):
        assert w.get(t, 0.0) == cap.get(t, 0.0) or t not in cap
    if E > 0:
        assert set(w) >= set("ABCDE") and "F" not in w and "510300" not in w
        assert all(abs(w[t] - E / 5) < 1e-12 for t in "ABCDE")
    else:
        assert not any(t in w for t in "ABCDE")


def test_hybrid_fallbacks_fewer_than_five_or_none():
    from research_v3.sector_mom.hybrid import hybrid_weights
    cap = {"510300": 1 / 3, "159915": 1 / 3, "518880": 1 / 3, "CASH": 0.0}
    w3 = hybrid_weights(cap, ["A", "B", "C"])
    assert abs(sum(w3.values()) - 1.0) < 1e-12 and all(abs(w3[t] - (2 / 3) / 3) < 1e-12 for t in "ABC") and w3["518880"] == 1 / 3
    w0 = hybrid_weights(cap, [])                              # none eligible: keep CAP_DEF_M's own equity ETFs
    assert w0 == {"518880": 1 / 3, "510300": 1 / 3, "159915": 1 / 3}


def test_hybrid_engine_reproduces_cap_def_when_replacement_disabled():
    from research_v3.sector_mom.hybrid import hybrid_weights
    s = sessions_from("2020-01-02", 40)
    rng = np.random.default_rng(2)
    ser = {t: (lambda c: (c.copy(), c))(10 * np.exp(np.cumsum(rng.normal(0, .01, 40)))) for t in ("510300", "511010", "SECT")}
    pan = toy_panel(ser, s, sector=["SECT"])
    rows = [(5, 6, {"510300": 1 / 3, "511010": 1 / 3}), (15, 16, {"510300": 2 / 3}), (25, 26, {"511010": 1 / 3})]
    cap = Plan(weekly=[(a, b, w) for a, b, w in rows])
    hyb = Plan(weekly=[(a, b, hybrid_weights({**w, "CASH": 0}, ["SECT"], replace=False)) for a, b, w in rows])
    a = simulate(pan, cap, StratSpec("C", "mom"), PRIMARY, 1e6, False, FlatCurve(), 6, 39, postpone_on_missing=True)
    b = simulate(pan, hyb, StratSpec("H", "mom"), PRIMARY, 1e6, False, FlatCurve(), 6, 39, postpone_on_missing=True)
    assert (a.nav.nav.to_numpy() == b.nav.nav.to_numpy()).all()
    hyb_on = Plan(weekly=[(a_, b_, hybrid_weights({**w, "CASH": 0}, ["SECT"], replace=True)) for a_, b_, w in rows])
    c = simulate(pan, hyb_on, StratSpec("H", "mom"), PRIMARY, 1e6, False, FlatCurve(), 6, 39, postpone_on_missing=True)
    assert not (a.nav.nav.to_numpy() == c.nav.nav.to_numpy()).all()


def test_postponement_waits_until_all_symbols_have_bars():
    s = sessions_from("2020-01-02", 12)
    o1, c1 = np.full(12, 10.0), np.full(12, 10.0)
    o2 = np.full(12, 5.0); c2 = np.full(12, 5.0)
    o2[1] = c2[1] = np.nan                                     # Y suspended on the execution session
    pan = toy_panel({"X": (o1, c1), "Y": (o2, c2)}, s)
    r = simulate(pan, Plan(weekly=[(0, 1, {"X": 0.5, "Y": 0.5})]), StratSpec("S", "mom"), ZERO, 1e6, False, FlatCurve(), 1, 11,
                 postpone_on_missing=True)
    assert r.postponements and r.postponements[0]["date"] == s[1]
    assert {t["date"] for t in r.trades} == {s[2]}


# ------------------------------------------------------------------------------------------------ phase 2 gates (temp dirs, no real data)
def test_phase2_refuses_without_authorization(tmp_path):
    from research_v3.sector_mom.hybrid_phase2 import Phase2Refused, run_phase2
    called = []
    with pytest.raises(Phase2Refused):
        run_phase2(tmp_path / "AUTH.json", tmp_path / "state.json", tmp_path / "out", core=lambda d: called.append(1))
    assert not called and not (tmp_path / "state.json").exists()
    (tmp_path / "AUTH.json").write_text('{"authorized": false}')
    with pytest.raises(Phase2Refused):
        run_phase2(tmp_path / "AUTH.json", tmp_path / "state.json", tmp_path / "out", core=lambda d: called.append(1))
    assert not called


def test_phase2_second_run_refused_and_guard_stays_closed(tmp_path):
    from research_v3.sector_mom.hybrid_phase2 import Phase2Refused, run_phase2
    (tmp_path / "AUTH.json").write_text('{"authorized": true}')
    calls = []
    r = run_phase2(tmp_path / "AUTH.json", tmp_path / "state.json", tmp_path / "out", core=lambda d: calls.append(1) or {"ok": 1})
    assert r == {"ok": 1} and json.loads((tmp_path / "state.json").read_text())["status"] == "completed"
    with pytest.raises(Phase2Refused):
        run_phase2(tmp_path / "AUTH.json", tmp_path / "state.json", tmp_path / "out", core=lambda d: calls.append(1))
    assert len(calls) == 1
    with pytest.raises(guard.HoldoutAccessError):             # the ordinary guard is untouched by the gate
        guard.assert_date_allowed("2024-10-08")


def test_phase2_crash_burns_the_one_time_state(tmp_path):
    from research_v3.sector_mom.hybrid_phase2 import Phase2Refused, run_phase2
    (tmp_path / "AUTH.json").write_text('{"authorized": true}')
    def boom(d): raise ValueError("x")
    with pytest.raises(ValueError):
        run_phase2(tmp_path / "AUTH.json", tmp_path / "state.json", tmp_path / "out", core=boom)
    with pytest.raises(Phase2Refused):
        run_phase2(tmp_path / "AUTH.json", tmp_path / "state.json", tmp_path / "out", core=lambda d: 1)


def test_phase2_outcome_classification():
    from research_v3.sector_mom.hybrid_phase2 import classify_outcome
    ok = {"xs_h": 1.0, "xs_c": 0.5, "mdd_h": 0.15, "mdd_c": 0.12}
    bad = {"xs_h": 0.4, "xs_c": 0.5, "mdd_h": 0.15, "mdd_c": 0.12}
    assert classify_outcome(ok, ok)["outcome"] == "SUPPORTED"
    assert classify_outcome(ok, bad)["outcome"] == "WEAK"
    assert classify_outcome(bad, ok)["outcome"] == "NOT_SUPPORTED"
    assert classify_outcome({**ok, "mdd_h": 0.18}, ok)["outcome"] == "NOT_SUPPORTED"      # > +5 pp drawdown
