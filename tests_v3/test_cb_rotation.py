"""Tests for research_v3.cb_rotation (development phase): protocol hash, holdout guard, look-ahead, eligibility filters,
forced exit, buffer rule, lots, price-limit regime, delisting terminal values, commission arithmetic, classification."""
import json

import numpy as np
import pandas as pd
import pytest

from research_v3.cb_rotation import data as cbdata
from research_v3.cb_rotation import guard
from research_v3.cb_rotation.classify import classify_one
from research_v3.cb_rotation.data import NEVER, CBPanel
from research_v3.cb_rotation.engine import (Costs, buy_blocked, buy_quantity, commission, double_low, eligibility,
                                            horizon_dates, sell_blocked, select_targets, simulate, weekly_signal_rows)
from research_v3.cb_rotation.protocol import ProtocolChangedError, load_verified


# ------------------------------------------------------------------------------------------------ helpers
def sessions_from(start, n):
    return [d.strftime("%Y-%m-%d") for d in pd.bdate_range(start, periods=n)]


def mk_panel(sessions, closes, premium=0.1, opens=None, volume=1e6, amount=None, st=None, notice=None, maturity=None,
             bars_prior=None, terminated=None, delist_type=None, last_bar=None):
    """closes: (S, B) array (NaN = no bar). premium scalar or (S,B) or per-bond list."""
    closes = np.asarray(closes, float)
    S, B = closes.shape
    opens = closes.copy() if opens is None else np.asarray(opens, float)
    prem = np.broadcast_to(np.asarray(premium, float), (S, B)).copy()
    prem[~np.isfinite(closes)] = np.nan
    vol = np.where(np.isfinite(closes), volume, np.nan)
    amt = np.where(np.isfinite(closes), (volume * closes) if amount is None else amount, np.nan)
    codes = [f"1{k:05d}" for k in range(B)]
    first = np.array([int(np.where(np.isfinite(closes[:, k]))[0][0]) for k in range(B)])
    last = np.array([int(np.where(np.isfinite(closes[:, k]))[0][-1]) for k in range(B)]) if last_bar is None else np.asarray(last_bar)
    p = CBPanel(codes, codes, codes, list(sessions), opens, np.maximum(opens, closes), np.minimum(opens, closes), closes,
                vol, amt, prem, np.zeros((S, B), bool) if st is None else st,
                np.full(B, NEVER) if notice is None else np.asarray(notice),
                np.full(B, NEVER) if maturity is None else np.asarray(maturity),
                np.full(B, 100) if bars_prior is None else np.asarray(bars_prior), last, first,
                np.zeros(B, bool) if terminated is None else np.asarray(terminated),
                np.full(B, "") if delist_type is None else np.asarray(delist_type))
    return p.finalize()


def accr(S, daily=0.0):
    return np.full(S, 1.0 + daily)


def run(p, start=3, end=None, signals=None, costs=Costs(), capital=25000.0, lots=True, mode="topn", passive_frac=0.0,
        accrual=None):
    E_full, E_exit = eligibility(p)
    S = p.n_sessions
    end = S - 1 if end is None else end
    sig = weekly_signal_rows(p.sessions) if signals is None else signals
    return simulate(p, accr(S) if accrual is None else accrual, start, end, sig, E_full, E_exit, capital, costs, lots,
                    mode=mode, passive_frac=passive_frac)


# ------------------------------------------------------------------------------------------------ protocol / guard
def test_protocol_hash_rejection(tmp_path):
    real = load_verified()
    assert real["protocol"].startswith("research_v3 cb_rotation")
    from research_v3.cb_rotation.protocol import PROTOCOL_PATH
    mod = tmp_path / "p.json"
    mod.write_bytes(PROTOCOL_PATH.read_bytes() + b" ")
    h = tmp_path / "p.sha256"
    h.write_text(PROTOCOL_PATH.with_suffix(".sha256").read_text())
    with pytest.raises(ProtocolChangedError):
        load_verified(mod, h)
    ok = tmp_path / "ok.json"
    ok.write_bytes(PROTOCOL_PATH.read_bytes())
    assert load_verified(ok, h)["written_at"] == "2026-10-19"


def test_holdout_guard(tmp_path):
    guard.assert_date_allowed("2023-12-31")
    with pytest.raises(guard.HoldoutAccessError):
        guard.assert_date_allowed("2024-01-01")
    with pytest.raises(guard.HoldoutAccessError):
        guard.assert_dates_allowed(["2023-12-29", "2024-02-01"])
    df = pd.DataFrame({"date": ["2023-12-29", "2024-01-02"], "x": [1, 2]})
    assert guard.clip_to_dev(df)["date"].tolist() == ["2023-12-29"]
    with pytest.raises(guard.HoldoutAccessError):
        with guard.open_holdout(tmp_path / "a.json", tmp_path / "s.json"):
            pass
    (tmp_path / "a.json").write_text(json.dumps({"authorized": False}))
    (tmp_path / "s.json").write_text(json.dumps({"status": "started"}))
    with pytest.raises(guard.HoldoutAccessError):
        with guard.open_holdout(tmp_path / "a.json", tmp_path / "s.json"):
            pass
    assert guard.current_limit() == "2023-12-31"


def test_loaders_clip_at_file_boundary(tmp_path):
    for sub in ("sina", "tx", "va"):
        (tmp_path / sub).mkdir()
    d = ["2023-12-28", "2023-12-29", "2024-01-02", "2024-01-03"]
    pd.DataFrame({"date": d, "open": 100.0, "high": 101.0, "low": 99.0, "close": [100, 101, 102, 103],
                  "volume": 10}).to_csv(tmp_path / "sina" / "110001.csv", index=False)
    pd.DataFrame({"date": d, "open": 100.0, "close": 100.0, "high": 1, "low": 1, "volume": 1, "turnover": 0,
                  "amount": 5e6}).to_csv(tmp_path / "tx" / "110001.csv", index=False)
    pd.DataFrame({"DATE": [x + " 00:00:00" for x in d], "SWAPVALUE": 90.0, "SWAPOR": 10.0, "SWAPPRICE": 10.0}
                 ).to_csv(tmp_path / "va" / "110001.csv", index=False)
    bars, terminated = cbdata.load_bars("110001", tmp_path)
    assert bars.index.max() == "2023-12-29" and not terminated        # real last bar is after the limit
    va = cbdata.load_va("110001", "2024-01-03", tmp_path)
    assert va.index.max() == "2023-12-29"
    # a bond that really ended inside development is flagged
    pd.DataFrame({"date": d[:2], "open": 100.0, "high": 101.0, "low": 99.0, "close": [100, 101], "volume": 10}
                 ).to_csv(tmp_path / "sina" / "110002.csv", index=False)
    b2, t2 = cbdata.load_bars("110002", tmp_path)
    assert t2 and len(b2) == 2
    # missing Tencent amount falls back to close x volume and is flagged
    assert not bars["amount_approx"].any()
    (tmp_path / "tx" / "110002.csv").write_text("date,open,close,high,low,volume,turnover,amount\n")
    assert b2["amount_approx"].all() and b2["amount"].iloc[0] == pytest.approx(1000.0)
    with pytest.raises(guard.HoldoutAccessError):
        cbdata.build_panel(["2023-12-29", "2024-01-02"], [], {}, {}, tmp_path)


# ------------------------------------------------------------------------------------------------ score look-ahead
def test_double_low_formula():
    assert double_low(np.array([110.0]), np.array([0.25]))[0] == pytest.approx(135.0)
    assert double_low(np.array([100.0]), np.array([-0.05]))[0] == pytest.approx(95.0)


def test_score_and_eligibility_use_only_data_up_to_signal():
    S, B = 60, 4
    sess = sessions_from("2020-01-01", S)
    rng = np.random.default_rng(1)
    close = 100 + np.cumsum(rng.normal(0, 1, (S, B)), axis=0)
    prem = rng.uniform(0, 0.3, (S, B))
    p = mk_panel(sess, close, premium=prem)
    E_full, E_exit = eligibility(p)
    s = 30
    ref_score = double_low(p.close, p.premium)[s].copy()
    close2, prem2 = close.copy(), prem.copy()
    close2[s + 1:] = 999.0
    prem2[s + 1:] = 5.0
    p2 = mk_panel(sess, close2, premium=prem2)
    E2_full, E2_exit = eligibility(p2)
    np.testing.assert_allclose(double_low(p2.close, p2.premium)[s], ref_score)
    np.testing.assert_array_equal(E_full[:s + 1], E2_full[:s + 1])
    np.testing.assert_array_equal(E_exit[:s + 1], E2_exit[:s + 1])


# ------------------------------------------------------------------------------------------------ eligibility filters
def elig_row(**kw):
    S = 40
    sess = sessions_from("2021-01-04", S)
    close = np.full((S, 1), 110.0)
    close[:, 0] += kw.pop("shift", 0)
    p = mk_panel(sess, close, **kw)
    return p, eligibility(p)


def test_filter_listed_for_10_sessions():
    p, (E, X) = elig_row(bars_prior=[0])
    assert E[9, 0] and not E[8, 0]


def test_filter_volume_on_signal_date():
    S = 40
    sess = sessions_from("2021-01-04", S)
    close = np.full((S, 1), 110.0)
    p = mk_panel(sess, close)
    p.volume[25, 0] = 0.0
    p.finalize()
    E, X = eligibility(p)
    assert not E[25, 0] and X[25, 0] and E[24, 0] and E[26, 0]


def test_filter_amount_threshold():
    vol = 5e6 / 110.0
    p, (E, X) = elig_row(volume=vol * 1.001)
    assert E[30, 0]
    p, (E, X) = elig_row(volume=vol * 0.99)
    assert not E[30, 0]


def test_filter_redemption_notice_same_day_as_signal():
    S = 40
    sess = sessions_from("2021-01-04", S)
    sig = 29
    p = mk_panel(sess, np.full((S, 1), 110.0), notice=[sess[sig]])
    E, X = eligibility(p)
    assert not E[sig, 0] and not X[sig, 0]            # notice published on the signal date -> ineligible
    assert E[sig - 1, 0]
    p = mk_panel(sess, np.full((S, 1), 110.0), notice=[sess[sig + 1]])
    E, X = eligibility(p)
    assert E[sig, 0] and not E[sig + 1, 0]


def test_filter_maturity_within_20_sessions():
    S = 60
    sess = sessions_from("2021-01-04", S)
    mat = sess[40]
    p = mk_panel(sess, np.full((S, 1), 110.0), maturity=[mat])
    E, X = eligibility(p)
    assert E[19, 0] and not E[20, 0]                  # session 20 sees maturity at session 40 = 20 sessions ahead
    assert horizon_dates(sess)[0] == sess[20]


def test_filter_st_and_price_cap():
    S = 40
    sess = sessions_from("2021-01-04", S)
    st = np.zeros((S, 1), bool)
    st[20:, 0] = True
    p = mk_panel(sess, np.full((S, 1), 110.0), st=st)
    E, X = eligibility(p)
    assert E[19, 0] and not E[20, 0] and not X[20, 0]
    c = np.full((S, 1), 110.0)
    c[25:, 0] = 150.01
    c[24, 0] = 150.0
    p = mk_panel(sess, c)
    E, X = eligibility(p)
    assert E[24, 0] and not E[25, 0]


# ------------------------------------------------------------------------------------------------ selection
def test_buffer_rule_keeps_held_while_rank_le_20():
    B = 30
    score = np.arange(B, dtype=float) + 100.0                  # bond k has rank k+1
    codes = [f"{k:06d}" for k in range(B)]
    held = np.zeros(B, bool)
    held[[2, 11, 19, 20, 25]] = True                           # ranks 3, 12, 20 stay; ranks 21 and 26 go
    elig = np.ones(B, bool)
    keep, new, rank = select_targets(score, elig, held, codes)
    assert keep == [2, 11, 19]
    assert new == [0, 1, 3, 4, 5, 6, 7]                       # lowest non-held fill the 7 open slots
    assert len(keep) + len(new) == 10
    # ineligible holding is dropped even with a good rank
    elig2 = elig.copy()
    elig2[2] = False
    keep2, new2, _ = select_targets(score, elig2, held, codes)
    assert 2 not in keep2 and 2 not in new2 and len(keep2) + len(new2) == 10


def test_selection_ties_broken_by_code():
    keep, new, _ = select_targets(np.full(15, 100.0), np.ones(15, bool), np.zeros(15, bool), [f"{k:06d}" for k in range(15)])
    assert new == list(range(10))


# ------------------------------------------------------------------------------------------------ commission / lots
def test_commission_min1_vs_min5_arithmetic():
    c1, c5 = Costs(min_commission=1.0), Costs(min_commission=5.0)
    assert commission(2200.0, c1) == pytest.approx(1.0)          # 1 bp of 2200 = 0.22 -> min 1
    assert commission(2200.0, c5) == pytest.approx(5.0)
    assert commission(200000.0, c1) == pytest.approx(20.0)       # 1 bp binds above 10,000
    assert commission(200000.0, c5) == pytest.approx(20.0)
    assert commission(0.0, c5) == 0.0


def test_lot_rounding_10_bonds():
    c = Costs()
    q = buy_quantity(2500.0, 110.0, c, 10)
    assert q == 20.0 and q % 10 == 0                              # 22.7 bonds -> 20
    assert buy_quantity(1000.0, 110.0, c, 10) == 0.0
    assert buy_quantity(2500.0, 110.0, c, None) == pytest.approx(2499.0 / 110.0, rel=1e-3)
    # exact budget: 10 bonds at 100 cost 1000 + commission 1 = 1001
    assert buy_quantity(1001.0, 100.0, c, 10) == 10.0
    assert buy_quantity(1000.99, 100.0, c, 10) == 0.0


def test_simulation_trades_are_whole_lots():
    S, B = 40, 14
    sess = sessions_from("2021-01-04", S)
    rng = np.random.default_rng(3)
    close = 105 + rng.normal(0, 2, (S, B)).cumsum(axis=0) * 0.2
    p = mk_panel(sess, close, premium=rng.uniform(0, 0.4, (S, B)))
    r = run(p, start=5, capital=25000.0)
    assert r.trades and all(t["qty"] % 10 == 0 for t in r.trades)
    assert r.nav["nav"].iloc[-1] > 20000


def test_costs_flow_through_simulation():
    S, B = 20, 12
    sess = sessions_from("2021-01-04", S)
    p = mk_panel(sess, np.full((S, B), 100.0), premium=np.tile(np.linspace(0, 0.2, B), (S, 1)))
    r1 = run(p, start=5, costs=Costs(min_commission=1.0))
    r5 = run(p, start=5, costs=Costs(min_commission=5.0))
    n = r1.counters["fills_buy"] + r1.counters["fills_sell"]
    assert r1.counters["fees"] == pytest.approx(n * 1.0)          # every order below 10,000 CNY pays the minimum
    assert r5.counters["fees"] == pytest.approx(n * 5.0)
    assert r5.nav["nav"].iloc[-1] < r1.nav["nav"].iloc[-1]
    # slippage 10 bp on every executed notional
    buy = [t for t in r1.trades if t["side"] == "BUY"][0]
    assert buy["price"] == pytest.approx(buy["open"] * 1.001)


# ------------------------------------------------------------------------------------------------ forced exit
def test_forced_exit_between_rebalances():
    S, B = 30, 12
    sess = sessions_from("2021-01-04", S)                         # row 0 = Monday
    prem = np.tile(np.linspace(0.0, 0.3, B), (S, 1))
    notice = np.full(B, NEVER, dtype=object)
    notice[0] = sess[12]                                         # Thursday of week 2, published after bond 0 is held
    p = mk_panel(sess, np.full((S, B), 100.0), premium=prem, notice=notice.astype(str))
    sig = weekly_signal_rows(sess)
    r = run(p, start=1, signals=sig)
    buys0 = [t for t in r.trades if t["code"] == p.codes[0] and t["side"] == "BUY"]
    sells0 = [t for t in r.trades if t["code"] == p.codes[0] and t["side"] == "SELL"]
    assert buys0 and sells0
    assert sells0[0]["reason"] == "forced_exit"
    assert sells0[0]["date"] == sess[13]                         # next open after the notice session, not the weekly signal
    assert sess[13] not in [sess[i + 1] for i in sig if i + 1 < S]
    assert r.counters["forced_exits"] == 1
    # bond 0 is never rebought after the notice
    assert not [t for t in r.trades if t["code"] == p.codes[0] and t["side"] == "BUY" and t["date"] > sess[12]]


# ------------------------------------------------------------------------------------------------ price limits
def test_price_limit_regime_starts_2022_08_01():
    assert not buy_blocked(125.0, 100.0, "2022-07-29", True)    # +25% open before the regime: tradable
    assert buy_blocked(120.0, 100.0, "2022-08-01", True)        # at +20% limit: blocked
    assert not buy_blocked(119.99, 100.0, "2022-08-01", True)
    assert not sell_blocked(70.0, 100.0, "2022-07-29", True)
    assert sell_blocked(80.0, 100.0, "2022-08-01", True)
    assert not sell_blocked(80.01, 100.0, "2022-08-02", True)
    assert buy_blocked(100.0, 100.0, "2021-01-04", False) and sell_blocked(100.0, 100.0, "2021-01-04", False)


def _limit_panel(start_date):
    S, B = 12, 11
    sess = sessions_from(start_date, S)
    close = np.full((S, B), 100.0)
    opens = close.copy()
    prem = np.tile(np.linspace(0.0, 0.3, B), (S, 1))
    return sess, close, opens, prem


def test_blocked_buy_retries_next_open_after_regime():
    sess, close, opens, prem = _limit_panel("2022-08-01")        # Monday
    # signal at Friday row 4; bond 0 opens at the +20% limit on row 5 (blocked) and normally on row 6
    opens[5, 0] = 120.0
    close[5, 0] = 119.0
    p = mk_panel(sess, close, premium=prem, opens=opens)
    r = run(p, start=1, signals=[4])
    b0 = [t for t in r.trades if t["code"] == p.codes[0] and t["side"] == "BUY"]
    assert b0 and b0[0]["date"] == sess[6]
    assert r.counters["blocked_buy"] >= 1


def test_same_gap_not_blocked_before_regime():
    sess, close, opens, prem = _limit_panel("2022-07-18")        # row 5 = 2022-07-25 (< 2022-08-01)
    opens[5, 0] = 125.0
    close[5, 0] = 124.0
    p = mk_panel(sess, close, premium=prem, opens=opens)
    r = run(p, start=1, signals=[4])
    b0 = [t for t in r.trades if t["code"] == p.codes[0] and t["side"] == "BUY"]
    assert b0 and b0[0]["date"] == sess[5] and r.counters["blocked_buy"] == 0


def test_blocked_sell_retries():
    sess, close, opens, prem = _limit_panel("2022-08-01")
    notice = np.full(11, NEVER, dtype=object)
    notice[0] = sess[7]
    opens[8, 0] = 80.0
    close[8, 0] = 81.0
    p = mk_panel(sess, close, premium=prem, opens=opens, notice=notice.astype(str))
    r = run(p, start=1, signals=[4])
    s0 = [t for t in r.trades if t["code"] == p.codes[0] and t["side"] == "SELL"]
    assert s0 and s0[0]["date"] == sess[9]               # row 8 open is at the -20% limit (blocked), row 9 fills
    assert r.counters["blocked_sell"] >= 1


# ------------------------------------------------------------------------------------------------ delisting terminal value
def _delist_panel(dtype):
    S, B = 16, 11
    sess = sessions_from("2021-03-01", S)
    close = np.full((S, B), 100.0)
    close[8:, 0] = np.nan                                        # bond 0 last bar at row 7
    close[7, 0] = 90.0
    prem = np.tile(np.linspace(0.0, 0.3, B), (S, 1))
    term = np.zeros(B, bool)
    term[0] = True
    typ = np.full(B, "", dtype=object)
    typ[0] = dtype
    p = mk_panel(sess, close, premium=prem, terminated=term, delist_type=typ.astype(str))
    return p


@pytest.mark.parametrize("dtype,frac,expected", [("passive", 0.0, 0.0), ("passive", 0.5, 0.5), ("redemption", 0.0, 1.0),
                                                 ("maturity", 0.0, 1.0)])
def test_delisting_terminal_values(dtype, frac, expected):
    p = _delist_panel(dtype)
    r = run(p, start=1, signals=[0], passive_frac=frac)
    assert len(r.terminal) == 1
    t = r.terminal[0]
    assert t["last_close"] == 90.0 and t["fraction"] == pytest.approx(expected)
    assert t["proceeds"] == pytest.approx(t["qty"] * 90.0 * expected)
    held_key = {"passive": "passive_delist_held", "redemption": "redemption_delist_held", "maturity": "maturity_delist_held"}[dtype]
    assert r.counters[held_key] == 1


def test_passive_delisting_loss_reduces_nav_by_position_value():
    p0 = _delist_panel("passive")
    r0 = run(p0, start=1, signals=[0], passive_frac=0.0)
    r5 = run(p0, start=1, signals=[0], passive_frac=0.5)
    q = r0.terminal[0]["qty"]
    assert r5.nav["nav"].iloc[-1] - r0.nav["nav"].iloc[-1] == pytest.approx(q * 90.0 * 0.5, rel=1e-6)


# ------------------------------------------------------------------------------------------------ misc
def test_cash_accrues_shibor_style():
    S = 8
    sess = sessions_from("2021-01-04", S)
    p = mk_panel(sess, np.full((S, 1), 100.0))
    r = run(p, start=1, signals=[], accrual=accr(S, 0.001))
    assert r.nav["nav"].iloc[-1] == pytest.approx(25000.0 * 1.001 ** (S - 1))


def test_weekly_signal_rows_are_last_session_of_iso_week():
    sess = sessions_from("2021-01-04", 15)
    rows = weekly_signal_rows(sess)
    assert [sess[i] for i in rows] == ["2021-01-08", "2021-01-15", "2021-01-22"]


def test_ew_mode_equal_weights_and_large_account():
    S, B = 12, 5
    sess = sessions_from("2021-01-04", S)
    p = mk_panel(sess, np.full((S, B), 100.0))
    r = run(p, start=1, signals=[4], capital=1_000_000.0, lots=False, mode="ew")
    buys = [t for t in r.trades if t["side"] == "BUY"]
    assert len(buys) == 5
    assert all(abs(t["notional"] - 200_000 * 0.999) < 400 for t in buys)


def test_classification_rule():
    assert classify_one("2021-06-01", "2021-05-01", "2026-01-01", False)[0] == "redemption"
    assert classify_one("2021-06-01", None, "2021-06-20", False)[0] == "maturity"
    assert classify_one("2021-06-01", "2020-01-01", "2026-01-01", False) == ("redemption", "stale_notice")
    assert classify_one("2021-06-01", "2021-07-01", "2026-01-01", False)[0] == "passive"      # notice after the last bar
    assert classify_one("2021-06-01", "2021-05-20", "2021-06-10", False)[0] == "redemption"   # notice wins near maturity
    assert classify_one("2021-06-01", "2021-05-01", "2026-01-01", True)[0] == "passive"       # 404 arrangement
    assert classify_one("2021-06-01", None, None, False)[0] == "passive"


def test_read_list_parses_missing_dates_as_none(tmp_path):
    pd.DataFrame({"SECURITY_CODE": ["110001", "110002"], "LISTING_DATE": ["2018-01-02 00:00:00", None],
                  "DELIST_DATE": [None, None], "NOTICE_DATE_SH": [None, "2020-03-03 00:00:00"], "NOTICE_DATE_HS": [None, None],
                  "EXPIRE_DATE": [None, None], "VALUE_DATE": ["2017-12-01 00:00:00", None], "CEASE_DATE": [None, None],
                  "BOND_EXPIRE": ["6", None], "SECURITY_NAME_ABBR": ["a", "b"], "CONVERT_STOCK_CODE": ["600000", "600001"],
                  "RATING": ["AA", None], "EXECUTE_REASON_SH": [None, None], "EXECUTE_REASON_HS": [None, None],
                  "REDEEM_TYPE": [None, None], "EXECUTE_PRICE_SH": [None, None], "EXECUTE_PRICE_HS": [None, None]}
                 ).to_csv(tmp_path / "em_cb_list_full.csv", index=False)
    lst = cbdata.read_list(tmp_path)
    assert lst["DELIST_DATE"].isna().all() and lst["LISTING_DATE"].iloc[0] == "2018-01-02" and lst["LISTING_DATE"].iloc[1] is None
    meta = cbdata.bond_meta(lst)
    assert list(meta["code"]) == ["110001"] and meta["notice"].iloc[0] is None
    assert meta["maturity"].iloc[0] == "2023-12-01"


def test_exit_eligibility_can_ignore_liquidity_for_sensitivity():
    S = 40
    sess = sessions_from("2021-01-04", S)
    vol = np.full((S, 1), 1e6)
    p = mk_panel(sess, np.full((S, 1), 110.0))
    p.amount[10:, 0] = 1e6                                         # amount collapses below 5m from session 10
    p.finalize()
    E, X = eligibility(p)
    E2, X2 = eligibility(p, exit_liquidity=False)
    assert not X[39, 0] and not E[39, 0]
    assert X2[39, 0] and not E2[39, 0]
