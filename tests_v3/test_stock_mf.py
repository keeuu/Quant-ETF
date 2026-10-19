"""Tests for research_v3.stock_mf (development phase): protocol hash, holdout guard, look-ahead, composite, buffer,
affordability, lots, fees, stamp duty, price limits, T+1, blocked retry, delisting, inferred-ST, adjustment maths."""
import hashlib
import json

import numpy as np
import pandas as pd
import pytest

from research_v3.stock_mf import guard
from research_v3.stock_mf.engine import (Costs, buy_blocked, buy_quantity, limit_pct, limit_prices, sell_blocked, simulate)
from research_v3.stock_mf.factors import (affordable_mask, biweekly_signals, composite, factors_at, month_end_indices,
                                          rank_table, select_targets, universe_at, universe_for_signal, weekly_signal_indices,
                                          winsorize_z)
from research_v3.stock_mf.panel import (StockBars, assemble, event_multipliers, infer_st, tx_total_return_prices)
from research_v3.stock_mf.protocol import ProtocolChangedError, load_verified


# ------------------------------------------------------------------------------------------------ helpers
def sessions_from(start, n):
    return [d.strftime("%Y-%m-%d") for d in pd.bdate_range(start, periods=n)]


def mk_stock(code, sessions, close, open_=None, ex="SH", volume=1e6, float_sh=1e8, delisted=False, high=None, low=None,
             adj=None, exr=None):
    close = np.asarray(close, float)
    open_ = close.copy() if open_ is None else np.asarray(open_, float)
    n = len(close)
    hi = np.maximum(open_, close) if high is None else np.asarray(high, float)
    lo = np.minimum(open_, close) if low is None else np.asarray(low, float)
    vol = np.full(n, volume) if np.isscalar(volume) else np.asarray(volume, float)
    adjc = close if adj is None else np.asarray(adj, float)
    adjo = open_ * (adjc / close)
    df = pd.DataFrame({"open": open_, "high": hi, "low": lo, "close": close, "volume": vol, "amount": vol * close,
                       "adj_open": adjo, "adj_close": adjc}, index=sessions[:n])
    df = df.dropna(subset=["close"])
    fr = pd.Series([float_sh], index=[sessions[0]])
    return StockBars(code, ex, df, pd.Series(dtype=float) if exr is None else exr, fr, delisted)


def toy(stocks, sessions):
    return assemble(stocks, sessions)


class FlatAccrual:
    @staticmethod
    def arr(n):
        return np.ones(n)


def run_toy(p, signals, targets_fn, capital=100_000.0, costs=None, lots=False, start=0, end=None):
    end = len(p.sessions) - 1 if end is None else end
    return simulate(p, np.ones(len(p.sessions)), start, end, signals, targets_fn, capital,
                    costs or Costs(2.5, 0.0, 0.1, 0.0), lots)


# ------------------------------------------------------------------------------------------------ protocol hash
def test_protocol_hash_ok_and_rejection(tmp_path):
    p = load_verified()
    assert "factors" in p and p["decision_on_development"]["version"].startswith("SMALL")
    proto = tmp_path / "p.json"
    proto.write_text('{"a": 1}')
    h = tmp_path / "p.sha256"
    h.write_text("0" * 64)
    with pytest.raises(ProtocolChangedError):
        load_verified(proto, h)
    h.write_text(hashlib.sha256(proto.read_bytes()).hexdigest())
    assert load_verified(proto, h) == {"a": 1}
    proto.write_text('{"a": 2}')
    with pytest.raises(ProtocolChangedError):
        load_verified(proto, h)


def test_committed_protocol_hash_value():
    assert hashlib.sha256((guard._HERE / "protocol_stock_mf.json").read_bytes()).hexdigest().startswith("87fd5306")


# ------------------------------------------------------------------------------------------------ holdout guard
def test_guard_default_limit_and_asserts():
    assert guard.DEV_END == "2024-03-31" and guard.HOLDOUT_START == "2024-04-01"
    assert guard.current_limit() == "2024-03-31"
    guard.assert_date_allowed("2024-03-29")
    guard.assert_date_allowed("2024-03-31")
    with pytest.raises(guard.HoldoutAccessError):
        guard.assert_date_allowed("2024-04-01")
    with pytest.raises(guard.HoldoutAccessError):
        guard.assert_dates_allowed(["2020-01-01", "2025-01-01"])


def test_guard_clip_to_dev():
    df = pd.DataFrame({"date": ["2024-03-28", "2024-04-01", "2026-01-01"], "x": [1, 2, 3]})
    out = guard.clip_to_dev(df, "date")
    assert out["date"].tolist() == ["2024-03-28"]


def test_open_holdout_requires_authorisation_and_started_state(tmp_path):
    auth, state = tmp_path / "auth.json", tmp_path / "state.json"
    with pytest.raises(guard.HoldoutAccessError):         # both missing
        with guard.open_holdout(auth, state):
            pass
    auth.write_text(json.dumps({"authorized": False}))
    state.write_text(json.dumps({"status": "started"}))
    with pytest.raises(guard.HoldoutAccessError):         # not authorised
        with guard.open_holdout(auth, state):
            pass
    auth.write_text(json.dumps({"authorized": True}))
    state.write_text(json.dumps({"status": "pending"}))
    with pytest.raises(guard.HoldoutAccessError):         # not started
        with guard.open_holdout(auth, state):
            pass
    state.write_text(json.dumps({"status": "started"}))
    with guard.open_holdout(auth, state):
        assert guard.current_limit() == guard.HOLDOUT_END
        guard.assert_date_allowed("2025-06-30")
    assert guard.current_limit() == guard.DEV_END         # restored
    with pytest.raises(guard.HoldoutAccessError):
        guard.assert_date_allowed("2025-06-30")


def test_no_authorisation_file_in_dev_phase():
    assert not (guard._HERE / "HOLDOUT_AUTHORIZATION.json").exists()
    assert not (guard._HERE / "holdout_state.json").exists()


def test_loader_clips_after_dev_end(tmp_path):
    from research_v3.stock_mf import panel as P
    d = tmp_path
    (d / "lists").mkdir()
    (d / "dumps").mkdir()
    (d / "ths_early").mkdir()
    pd.DataFrame({"thscode": ["600001.SH"], "ticker": ["600001"], "exchange": ["SH"], "list_date": ["2000-01-01"]}).to_csv(
        d / "lists" / "ths_tickers.csv", index=False)
    pd.DataFrame(columns=["code", "name", "list", "delist", "ex"]).to_csv(d / "lists" / "delist_sh_sz.csv", index=False)
    dates = ["2024-03-28", "2024-03-29", "2024-04-01", "2024-04-02", "2025-01-02"]
    ms = [int((pd.Timestamp(x, tz="Asia/Shanghai")).timestamp() * 1000) for x in dates]
    pd.DataFrame({"thscode": "600001.SH", "date_ms": ms, "open_price": 10.0, "high_price": 10.5, "low_price": 9.5,
                  "close_price": 10.0, "volume": 1e6, "turnover": 1e7}).to_csv(d / "dumps" / "daily_k.csv.gz", index=False)
    pd.DataFrame(columns=["thscode", "ex_date_ms", "dividend_per_share", "per_share_bonus", "allotment_ratio",
                          "allotment_price"]).to_csv(d / "dumps" / "adjustment_factors.csv.gz", index=False)
    stocks = P.load_dev_stocks(d)
    assert len(stocks) == 1
    assert stocks[0].df.index.max() == "2024-03-29"        # nothing after DEV_END reaches scoring code
    with pytest.raises(guard.HoldoutAccessError):
        P.load_dev_stocks(d, limit="2025-01-02")


# ------------------------------------------------------------------------------------------------ look-ahead
def random_panel(n_sess=160, n_stocks=6, seed=3):
    rng = np.random.default_rng(seed)
    sess = sessions_from("2020-01-01", n_sess)
    stocks = []
    for k in range(n_stocks):
        r = rng.normal(0, 0.02, n_sess)
        c = 10 * np.exp(np.cumsum(r))
        o = c * (1 + rng.normal(0, 0.003, n_sess))
        stocks.append(mk_stock(f"60{k:04d}", sess, c, o, volume=rng.uniform(5e5, 2e6, n_sess), float_sh=1e8 * (k + 1)))
    return toy(stocks, sess), sess


def test_factors_use_only_data_up_to_signal_date():
    p, sess = random_panel()
    s = 120
    ref = factors_at(p, s)
    # (a) identical on a panel truncated at s
    trunc = factors_at(p.truncated(s), s)
    # (b) identical when everything after s is destroyed
    q, _ = random_panel()
    for f_ in ("open", "high", "low", "close", "volume", "amount", "adj_open", "adj_close", "float_sh"):
        a = getattr(q, f_)
        a[s + 1:] = np.random.default_rng(9).uniform(1, 1e3, a[s + 1:].shape)
    q.finalize()
    corrupt = factors_at(q, s)
    for k in ref:
        assert np.allclose(ref[k], trunc[k], equal_nan=True), k
        assert np.allclose(ref[k], corrupt[k], equal_nan=True), k
        assert np.isfinite(ref[k]).all()


def test_factor_definitions_on_one_stock():
    p, sess = random_panel()
    s = 130
    f = factors_at(p, s)
    j = 2
    a = p.adj_close[:, j]
    assert f["REV"][j] == pytest.approx(-(a[s] / a[s - 20] - 1))
    r = a[s - 59:s + 1] / a[s - 60:s] - 1
    assert f["LOWVOL"][j] == pytest.approx(-np.std(r, ddof=1))
    turn = p.volume[s - 19:s + 1, j] / p.float_sh[s - 19:s + 1, j]
    assert f["LOWTURN"][j] == pytest.approx(-turn.mean())
    assert f["SIZE"][j] == pytest.approx(-np.log(p.close[s, j] * p.float_sh[s, j]))


def test_universe_and_signals_do_not_look_ahead():
    p, sess = random_panel(n_sess=400, n_stocks=8)
    m = month_end_indices(sess)[10]
    u = universe_at(p, m, size=5)
    u2 = universe_at(p.truncated(m), m, size=5)
    assert (u == u2).all()


# ------------------------------------------------------------------------------------------------ composite / selection
def test_winsorise_zscore_composite_on_toy_cross_section():
    x = np.arange(1.0, 101.0)                     # 1..100
    x[-1] = 10_000.0                              # outlier gets winsorised to the 99th percentile
    lo, hi = np.percentile(x, [1, 99])
    z = winsorize_z(x)
    w = np.clip(x, lo, hi)
    assert z[-1] == pytest.approx((hi - w.mean()) / w.std())
    assert z.mean() == pytest.approx(0, abs=1e-12) and z.std() == pytest.approx(1)
    fac = {"REV": x, "LOWVOL": -x, "LOWTURN": x.copy(), "SIZE": np.full(100, 5.0)}   # SIZE constant -> zeros
    mem = np.ones(100, bool)
    comp = composite(fac, mem)
    # REV and LOWTURN equal +z, LOWVOL is -x but winsorised at its own percentiles (mirror image)
    assert comp[10] == pytest.approx((z[10] + winsorize_z(-x)[10] + z[10] + 0) / 4)
    # missing factor or non-member -> NaN
    fac2 = {k: v.copy() for k, v in fac.items()}
    fac2["REV"][3] = np.nan
    mem2 = mem.copy()
    mem2[7] = False
    c2 = composite(fac2, mem2)
    assert np.isnan(c2[3]) and np.isnan(c2[7]) and np.isfinite(c2[8])


def make_table(scores):
    comp = np.array(scores, float)
    codes = [f"{i:06d}" for i in range(len(comp))]
    return rank_table(comp, codes)


def test_buffer_rule_keep_while_rank_le_20():
    scores = np.linspace(30, 1, 30)             # column j has rank j+1
    t = make_table(scores)
    held = [11, 19, 20, 25]                       # ranks 12, 20, 21, 26
    tgt = select_targets(t, held, n=10, keep_rank=20)
    assert 11 in tgt and 19 in tgt                # rank <= 20 kept
    assert 20 not in tgt and 25 not in tgt        # rank 21 and 26 dropped
    assert len(tgt) == 10
    assert tgt[:2] == [11, 19]
    assert set(tgt[2:]) == {0, 1, 2, 3, 4, 5, 6, 7}   # slots filled by the best-ranked non-held stocks
    # a holding that left the ranked universe (NaN composite) is not kept
    scores2 = scores.copy()
    scores2[11] = np.nan
    assert 11 not in select_targets(make_table(scores2), [11], n=10)
    # empty book -> plain top 10
    assert select_targets(t, [], n=10) == list(range(10))


def test_affordability_filter_and_star_lot():
    sess = sessions_from("2022-01-03", 5)
    stocks = [mk_stock("600001", sess, [20.0] * 5), mk_stock("600002", sess, [26.0] * 5),
              mk_stock("688001", sess, [12.0] * 5), mk_stock("688002", sess, [13.0] * 5)]
    for s_ in stocks:
        s_.df["volume"] = 1e6
    p = toy(stocks, sess)
    ok = affordable_mask(p, 2)
    assert ok.tolist() == [True, False, True, False]    # 100*20=2000 ok; 2600 no; STAR 200*12=2400 ok; 200*13=2600 no
    assert p.lot.tolist() == [100, 100, 200, 200]
    t = make_table([4, 3, 2, 1])
    assert select_targets(t, [], n=2, affordable=ok) == [0, 2]    # unaffordable names are skipped before ranking
    assert select_targets(t, [1], n=2, affordable=ok) == [0, 2]   # a held name that became unaffordable is dropped


def test_lot_rounding_star_200():
    c = Costs(2.5, 0.0, 0.1, 0.0)
    assert buy_quantity(2500.0, 10.0, c, 100) == 200.0           # 250 -> 200 (floor to lots)
    assert buy_quantity(2500.0, 10.0, c, 200) == 200.0
    assert buy_quantity(2500.0, 13.0, c, 200) == 0.0             # a 200-share lot costs 2600 > budget
    assert buy_quantity(5000.0, 10.0, c, 200) == 400.0
    assert buy_quantity(5000.0, 10.0, c, 100) == 400.0           # 499.x -> 400
    q = buy_quantity(2500.0, 10.0, c, None)                      # fractional
    assert q == pytest.approx(2500.0 / (10.0 * (1 + 2.5e-4 + 1e-5)))
    # engine: STAR stock is bought in 200-share lots
    sess = sessions_from("2022-01-03", 6)
    p = toy([mk_stock("688001", sess, [10.0] * 6)], sess)
    res = run_toy(p, {0}, lambda i, held, pv: {0: 5300.0}, capital=20_000.0, lots=True)
    buys = [t for t in res.trades if t["side"] == "B"]
    assert len(buys) == 1 and buys[0]["shares"] == 400.0 and buys[0]["shares"] % 200 == 0


# ------------------------------------------------------------------------------------------------ fees
def test_min5_vs_no_min_fee_arithmetic():
    nm, m5 = Costs(2.5, 0.0, 0.1, 10), Costs(2.5, 5.0, 0.1, 10)
    n = 2500.0
    assert nm.commission(n) == pytest.approx(0.625)
    assert m5.commission(n) == 5.0                                    # minimum binds on a 2.5k order
    assert nm.buy_fees(n) == pytest.approx(0.625 + 0.025)
    assert m5.buy_fees(n) == pytest.approx(5.0 + 0.025)
    big = 1_000_000.0
    assert m5.commission(big) == pytest.approx(nm.commission(big)) == pytest.approx(250.0)   # min irrelevant when large
    # exact quantity with the minimum fee: cost must fit in the budget and be tight
    q = buy_quantity(2500.0, 10.0, m5, None)
    cost = q * 10.0 * (1 + 1e-5) + 5.0
    assert cost == pytest.approx(2500.0)
    assert buy_quantity(4.0, 10.0, m5, None) == 0.0                   # cannot even pay the minimum


def test_stamp_duty_switch_2023_08_28():
    c = Costs(2.5, 0.0, 0.1, 0.0)
    assert Costs.stamp_rate("2023-08-25") == 0.001
    assert Costs.stamp_rate("2023-08-28") == 0.0005
    assert c.sell_fees(10_000.0, "2023-08-25") == pytest.approx(10_000 * (2.5e-4 + 1e-5 + 1e-3))
    assert c.sell_fees(10_000.0, "2023-08-28") == pytest.approx(10_000 * (2.5e-4 + 1e-5 + 5e-4))
    assert c.buy_fees(10_000.0) == pytest.approx(10_000 * (2.5e-4 + 1e-5))     # no stamp duty on buys


# ------------------------------------------------------------------------------------------------ price limits
def test_chinext_limit_switch_2020_08_24():
    assert limit_pct("CHINEXT", "2020-08-21", False) == 0.10
    assert limit_pct("CHINEXT", "2020-08-24", False) == 0.20
    assert limit_pct("MAIN", "2022-01-04", False) == 0.10
    assert limit_pct("STAR", "2019-08-01", False) == 0.20
    assert limit_pct("MAIN", "2022-01-04", True) == 0.05 and limit_pct("CHINEXT", "2022-01-04", True) == 0.05
    up, dn = limit_prices(10.0, 0.10)
    assert (up, dn) == (11.0, 9.0)
    assert buy_blocked(11.0, 10.0, limit_pct("CHINEXT", "2020-08-21", False), True)        # +10% open: blocked pre-switch
    assert not buy_blocked(11.0, 10.0, limit_pct("CHINEXT", "2020-08-24", False), True)    # allowed after
    assert sell_blocked(9.0, 10.0, 0.10, True) and not sell_blocked(9.01, 10.0, 0.10, True)
    assert limit_prices(3.33, 0.10) == (3.66, 3.0)       # tick rounding to 2 decimals (half-up)
    assert buy_blocked(10.0, 10.0, 0.10, False) and sell_blocked(10.0, 10.0, 0.10, False)   # no bar -> both blocked


# ------------------------------------------------------------------------------------------------ engine behaviours
def test_t_plus_1_and_next_open_execution():
    sess = sessions_from("2022-01-03", 8)
    close = [10, 10, 10, 10, 10, 10, 10, 10.0]
    opn = [10, 10, 10.2, 10.4, 10.6, 10.8, 11.0, 11.0]
    p = toy([mk_stock("600001", sess, close, opn)], sess)
    sig = {0, 1}                                              # buy order at close 0, sell order at close 1 (the buy day)
    def fn(i, held, pv):
        return {0: 5000.0} if i == 0 else {}                  # i == 1: empty target -> sell everything held
    res = run_toy(p, sig, fn, capital=20_000.0)
    tr = res.trades
    assert [(t["date"], t["side"]) for t in tr] == [(sess[1], "B"), (sess[2], "S")]
    assert tr[0]["price"] == pytest.approx(10.0) and tr[1]["price"] == pytest.approx(10.2)   # open of next session
    assert res.counters["t1_blocked"] == 0
    # a sale order that reaches the engine on the buy day itself is refused (T+1 guard)
    res2 = run_toy(p, {0}, lambda i, h, pv: {0: 5000.0}, capital=20_000.0)
    assert res2.held_end                                  # still held, no same-day sale


def test_blocked_buy_retries_until_open_is_below_limit_up():
    sess = sessions_from("2022-01-03", 8)
    close = [10.0, 10.0, 11.0, 11.0, 11.0, 11.0, 11.0, 11.0]
    opn = [10.0, 11.0, 11.0, 11.0, 10.9, 10.9, 10.9, 10.9]   # 11.0 = prev close 10 * 1.1 -> limit-up open on day 1
    p = toy([mk_stock("600001", sess, close, opn)], sess)
    res = run_toy(p, {0}, lambda i, h, pv: {0: 5000.0}, capital=20_000.0)
    assert res.counters["blocked_buy"] >= 2                   # days 1 (open 11.0 = up-limit 11.0) and 2 (up-limit 12.1? open 11.0)
    buys = [t for t in res.trades if t["side"] == "B"]
    assert len(buys) == 1 and buys[0]["date"] in sess[1:5]
    # force: open at limit-up on days 1-3 then free on day 4 -> filled on day 4
    close = [10.0, 11.0, 12.1, 13.31, 13.0, 13.0, 13.0, 13.0]
    opn = [10.0, 11.0, 12.1, 13.31, 13.0, 13.0, 13.0, 13.0]
    p = toy([mk_stock("600001", sess, close, opn)], sess)
    res = run_toy(p, {0}, lambda i, h, pv: {0: 5000.0}, capital=20_000.0)
    buys = [t for t in res.trades if t["side"] == "B"]
    assert [b["date"] for b in buys] == [sess[4]] and res.counters["blocked_buy"] == 3


def test_blocked_sell_retries_at_limit_down_and_suspension():
    sess = sessions_from("2022-01-03", 10)
    close = [10, 10, 10, 10, 9.0, 8.1, 8.1, 8.1, 8.1, 8.1]
    opn = [10, 10, 10, 10, 9.0, 8.1, 8.2, 8.2, 8.2, 8.2]      # day 4 and 5 open at limit-down; day 6 free
    p = toy([mk_stock("600001", sess, close, opn)], sess)
    def fn(i, held, pv):
        return {0: 5000.0} if i == 0 else ({} if i == 3 else {0: None})
    res = run_toy(p, {0, 3}, fn, capital=20_000.0)
    sells = [t for t in res.trades if t["side"] == "S"]
    assert [s_["date"] for s_ in sells] == [sess[6]] and res.counters["blocked_sell"] == 2
    # suspended stock (no bar) cannot be sold; value carried forward at last close
    close2 = [10, 10, 10, np.nan, np.nan, np.nan, 10, 10, 10, 10.0]
    opn2 = close2.copy()
    p2 = toy([mk_stock("600001", sess, close2, opn2), mk_stock("600002", sess, [5.0] * 10)], sess)
    def fn2(i, held, pv):
        return {0: 5000.0} if i == 0 else ({} if i == 2 else {})
    res2 = run_toy(p2, {0, 2}, fn2, capital=20_000.0)
    sells = [t for t in res2.trades if t["side"] == "S"]
    assert sells and sells[0]["date"] == sess[6] and res2.counters["blocked_sell"] == 3
    nav_susp = res2.nav.set_index("date").loc[sess[3], "nav"]
    assert nav_susp == pytest.approx(res2.nav.set_index("date").loc[sess[2], "nav"])   # carried forward


def test_new_signal_replaces_unfilled_orders():
    sess = sessions_from("2022-01-03", 8)
    close = [10.0, 11.0, 12.1, 13.31, 13.0, 13.0, 13.0, 13.0]
    p = toy([mk_stock("600001", sess, close), mk_stock("600002", sess, [10.0] * 8)], sess)
    def fn(i, held, pv):
        return {0: 5000.0} if i == 0 else {1: 5000.0}
    res = run_toy(p, {0, 1}, fn, capital=20_000.0)
    codes = {t["code"] for t in res.trades}
    assert codes == {"600002"}                                 # the blocked order for 600001 was replaced


def test_delisting_conversion_at_last_close():
    sess = sessions_from("2022-01-03", 10)
    close = [10, 10, 10, 8.0, 6.0, 5.0, np.nan, np.nan, np.nan, np.nan]   # last bar day 5
    p = toy([mk_stock("600001", sess, close, delisted=True), mk_stock("600002", sess, [5.0] * 10)], sess)
    res = run_toy(p, {0}, lambda i, h, pv: {0: 5000.0}, capital=20_000.0)
    assert res.counters["delisted_holdings"] == 1
    nv = res.nav.set_index("date")
    shares = [t for t in res.trades if t["side"] == "B"][0]["shares"]
    cash_before = nv.loc[sess[5], "cash"]
    assert nv.loc[sess[6], "n_hold"] == 0
    assert nv.loc[sess[6], "cash"] == pytest.approx(cash_before + shares * 5.0)      # converted at the last close
    assert nv.loc[sess[9], "nav"] == pytest.approx(nv.loc[sess[6], "nav"])
    assert not res.held_end
    # not delisted flag -> position stays and is carried at the last close
    p2 = toy([mk_stock("600001", sess, close, delisted=False), mk_stock("600002", sess, [5.0] * 10)], sess)
    res2 = run_toy(p2, {0}, lambda i, h, pv: {0: 5000.0}, capital=20_000.0)
    assert res2.counters["delisted_holdings"] == 0 and res2.held_end


def test_dividend_reinvested_via_adjusted_prices_and_cash_accrual():
    sess = sessions_from("2022-01-03", 6)
    adj = np.array([10, 10, 10, 11, 11, 11.0])                # +10% total return via adjusted series, raw flat
    p = toy([mk_stock("600001", sess, [10.0] * 6, adj=adj)], sess)
    acc = np.full(6, 1.001)
    res = simulate(p, acc, 0, 5, {0}, lambda i, h, pv: {0: 10_000.0}, 20_000.0, Costs(0, 0, 0, 0), False)
    nav = res.nav["nav"].to_numpy()
    assert nav[4] > nav[1] * 1.04                              # +10% on half the book, plus cash accrual


# ------------------------------------------------------------------------------------------------ inferred ST
def _st_series(rets, n_pad=0):
    """close built from daily returns; high/low such that close==high on up days and close==low on down days."""
    c = [10.0]
    for r in rets:
        c.append(c[-1] * (1 + r))
    c = np.array(c)
    up = np.diff(c, prepend=c[0]) >= 0
    high = np.where(up, c, c * 1.001)
    low = np.where(up, c * 0.999, c)
    return c, high, low


def test_inferred_st_rule_on_constructed_series():
    rng = np.random.default_rng(1)
    quiet = list(rng.normal(0, 0.01, 40))
    # (1) one +5% limit-close and no >5.5% move in the window -> flagged
    c, h, l = _st_series(quiet + [0.05] + quiet[:10])
    st = infer_st(c[:, None], h[:, None], l[:, None], c[:, None])[:, 0]
    assert st[41] and st[-1] and not st[39]
    # (2) a down limit-close at -5% also counts (close == low)
    c, h, l = _st_series(quiet + [-0.05] + quiet[:5])
    assert infer_st(c[:, None], h[:, None], l[:, None], c[:, None])[41, 0]
    # (3) a single +10% move in the window cancels the flag
    c, h, l = _st_series(quiet + [0.05] + quiet[:5] + [0.10] + quiet[:3])
    st = infer_st(c[:, None], h[:, None], l[:, None], c[:, None])[:, 0]
    assert st[41] and not st[47]
    # (4) the flag expires after 60 sessions without another 5% limit close
    c, h, l = _st_series([0.05] + list(rng.normal(0, 0.01, 80)))
    st = infer_st(c[:, None], h[:, None], l[:, None], c[:, None])[:, 0]
    assert st[1] and st[60] and not st[61]
    # (5) a +5% move that did not close at the high is not a limit close; 4% / 6% do not qualify either
    c, h, l = _st_series(quiet + [0.05])
    h2 = h.copy()
    h2[-1] = c[-1] * 1.02
    assert not infer_st(c[:, None], h2[:, None], l[:, None], c[:, None])[-1, 0]
    c, h, l = _st_series(quiet + [0.04])
    assert not infer_st(c[:, None], h[:, None], l[:, None], c[:, None])[-1, 0]
    c, h, l = _st_series(quiet + [0.06])
    assert not infer_st(c[:, None], h[:, None], l[:, None], c[:, None])[-1, 0]


# ------------------------------------------------------------------------------------------------ adjustment maths
def test_ths_event_multipliers_bonus_and_dividend():
    dates = np.array(sessions_from("2022-01-03", 6))
    closes = np.array([10, 10, 5.25, 5.3, 5.3, 5.3])
    ev = pd.DataFrame([{"ex": dates[2], "dividend_per_share": 0.5, "per_share_bonus": 1.0, "allotment_ratio": 0.0,
                        "allotment_price": 0.0}])
    F, exr, sk = event_multipliers(dates, closes, ev)
    R = (10 - 0.5) / 2.0                    # ex-right reference 4.75
    M = 10 / R
    assert F[:2].tolist() == [1, 1] and F[2:] == pytest.approx([M] * 4)
    assert exr[2] == pytest.approx(1 / M) and sk == 0
    adj = closes * F
    assert adj[2] / adj[1] - 1 == pytest.approx(5.25 / R - 1)       # total return of the ex-day equals the market move vs reference
    # rights issue above the market price is ignored
    ev2 = pd.DataFrame([{"ex": dates[2], "dividend_per_share": 0.0, "per_share_bonus": 0.0, "allotment_ratio": 0.3,
                         "allotment_price": 50.0}])
    F2, _, _ = event_multipliers(dates, closes, ev2)
    assert F2.tolist() == [1] * 6


def test_tencent_hfq_rebuild_matches_total_return():
    rng = np.random.default_rng(5)
    n = 120
    raw = np.round(10 * np.exp(np.cumsum(rng.normal(0, 0.02, n))), 2)
    # build Tencent-style hfq: hfq = S*raw + D with S = 1.4 and a cash dividend of 0.30 (hfq units) on day 60
    S, D = 1.4, np.where(np.arange(n) >= 60, 0.42, 0.0)
    hfq = np.round(S * raw + D, 2)
    # the day-60 raw already includes the ex-dividend drop; reference total return = (raw60 + 0.30)/raw59 - 1
    adj, adj_o, n_act, n_chg = tx_total_return_prices(raw, hfq, raw)
    r = adj[1:] / adj[:-1] - 1
    expected = raw[1:] / raw[:-1] - 1
    expected[59] = (raw[60] + 0.30) / raw[59] - 1
    assert n_act >= 1
    assert np.abs(r - expected).max() < 5e-3                      # rounding noise only
    naive = hfq[1:] / hfq[:-1] - 1
    assert np.abs(naive - expected).max() > 5e-3 or np.abs(naive - expected).mean() > np.abs(r - expected).mean()


# ------------------------------------------------------------------------------------------------ calendar / universe
def test_signal_calendar_biweekly_and_universe_timing():
    sess = sessions_from("2021-12-27", 120)
    wk = weekly_signal_indices(sess)
    assert all(pd.Timestamp(sess[i]).dayofweek == 4 for i in wk[:-1])        # last session of week = Friday here
    p, _ = random_panel(n_sess=400, n_stocks=8)
    s = p.sessions
    univs = {m: universe_at(p, m, size=4) for m in month_end_indices(s) if m >= 260}
    first = min(univs)
    assert universe_for_signal(univs, first) is None                          # strictly before the signal row
    sigs = biweekly_signals(s, univs, s[first + 1])
    allwk = [i for i in weekly_signal_indices(s) if s[i] >= s[first + 1] and universe_for_signal(univs, i) is not None]
    assert sigs == allwk[::2]
    for i in sigs:
        m = max(k for k in univs if k < i)
        assert m < i


# ------------------------------------------------------------------------------------------------ independent recompute (real data)
from research_v3.stock_mf.panel import DATA_DIR as _DATA_DIR  # noqa: E402

_HAVE_DATA = (_DATA_DIR / "manifest.json").exists()


@pytest.mark.skipif(not _HAVE_DATA, reason="local stock_mf raw data not downloaded")
def test_independent_pandas_recompute_of_top10_on_three_random_dev_signal_dates():
    """Recomputes universe, inferred-ST, the four factors, winsorise/z-score, composite, affordability and top 10 with
    per-stock pandas code (independent_check.py) on 3 random development signal dates and compares with the dense-array
    pipeline used by the runner."""
    from research_v3.stock_mf.independent_check import independent_top
    from research_v3.stock_mf.panel import assemble as _assemble, load_dev_stocks
    from research_v3.stock_mf.runner import Context
    stocks = [s for s in load_dev_stocks() if len(s.df) >= 250]
    panel = _assemble(stocks)
    ctx = Context(panel)
    rng = np.random.default_rng(20261019)
    picks = rng.choice(len(ctx.signals), size=3, replace=False)
    for k in picks:
        s = ctx.signals[int(k)]
        m = max(x for x in ctx.univs if x < s)
        table = ctx.tables[s]
        mine = select_targets(table, [], affordable=ctx.afford[s])
        mine_codes = [panel.codes[j] for j in mine]
        indep, frame = independent_top(stocks, panel.sessions, panel.sessions[s], panel.sessions[m])
        assert len(indep) == 10
        assert mine_codes == indep, (panel.sessions[s], mine_codes, indep)
        # no holdout date was touched
        assert panel.sessions[-1] <= guard.DEV_END


def test_fractional_account_fully_sells_dropped_names_and_never_holds_dust():
    n_st, n_days = 15, 120
    sess = sessions_from("2022-01-03", n_days)
    rng = np.random.default_rng(11)
    stocks = [mk_stock(f"60{k:04d}", sess, 10 * np.exp(np.cumsum(rng.normal(0, 0.005, n_days)))) for k in range(n_st)]
    p = toy(stocks, sess)
    sigs = set(range(0, n_days - 1, 5))

    def fn(i, held, pv):
        tgt = [(i // 5 + k) % n_st for k in range(10)]            # rotating 10 names
        out = {j: (None if j in held else pv / 10) for j in tgt}
        return out
    res = run_toy(p, sigs, fn, capital=1_000_000.0, lots=False)
    assert res.nav["n_hold"].max() <= 10
    assert len(res.held_end) <= 10
    # exhaust the cash: a second buy with no cash left must not create a dust position
    p2 = toy([mk_stock("600001", sess, [10.0] * n_days), mk_stock("600002", sess, [10.0] * n_days)], sess)
    res2 = run_toy(p2, {0}, lambda i, h, pv: {0: pv, 1: pv}, capital=100_000.0, lots=False)
    assert set(res2.held_end) == {0}
    # a held name that leaves the target is fully sold at the next open
    def fn3(i, held, pv):
        return {0: pv / 2} if i == 0 else {1: pv / 2}
    res3 = run_toy(p2, {0, 3}, fn3, capital=100_000.0, lots=False)
    assert set(res3.held_end) == {1}


# ------------------------------------------------------------------------------------------------ amendment 1
def test_amendment1_hash_verified_and_refuses_mismatch(tmp_path):
    from research_v3.stock_mf import protocol as P
    a = P.load_verified_amendment1()
    assert a["new_strategy"]["name"] == "LOWTURN_MONTHLY"
    bad = tmp_path / "a.json"
    bad.write_text("{}")
    h = tmp_path / "a.sha256"
    h.write_text("0" * 64)
    with pytest.raises(ProtocolChangedError):
        P.load_verified(bad, h)


def test_monthly_signal_dates_are_month_end_sessions():
    from research_v3.stock_mf.factors import monthly_signals
    sess = sessions_from("2021-01-01", 400)
    univs = {i: np.ones(3, bool) for i in month_end_indices(sess)}
    sigs = monthly_signals(sess, univs, "2021-03-01")
    me = set(month_end_indices(sess))
    assert sigs and all(i in me for i in sigs)
    for i in sigs:
        if i + 1 < len(sess):
            assert sess[i][:7] != sess[i + 1][:7]            # next session is in a later month
        assert universe_for_signal(univs, i) is not None      # strictly earlier month-end universe exists
    assert len(sigs) == len(set(sigs))


def test_buffer_rank_30_keeps_until_rank_31():
    t = make_table(np.linspace(40, 1, 40))             # column j has rank j+1
    tgt = select_targets(t, [24, 29, 30, 35], n=10, keep_rank=30)
    assert 24 in tgt and 29 in tgt                      # ranks 25 and 30 kept
    assert 30 not in tgt and 35 not in tgt              # ranks 31 and 36 dropped
    assert 29 not in select_targets(t, [29], n=10, keep_rank=20)   # the original buffer would drop rank 30
