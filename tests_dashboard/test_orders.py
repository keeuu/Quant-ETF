"""Order list (dashboard.orders): lot rounding, fees, leftover-cash routing, and that it never touches signals/NAV."""
from __future__ import annotations

import copy
import json
from datetime import datetime, timezone

import pandas as pd
import pytest

from dashboard import config
from dashboard.fetch import FetchResult, load_snapshot, write_snapshot
from dashboard.orders import DISCLAIMER, commission, compute_orders, floor_shares, plan
from dashboard.render import render_page
from dashboard.shadow import compute_market_view
from research_v3.cn_transfer import data as cn_data
from research_v3.cn_transfer.calendar_cn import get_xshg_calendar

UTC = timezone.utc
CN_UNI = ("510300", "510050", "510500", "159915", "518880", "511010")


def _plan(capital, cur_w, tgt_w, prices, lot=100, bp=2.5, min_fee=5.0, proxy_price=100.9, hold=True):
    return plan(capital=capital, cur_w=cur_w, tgt_w=tgt_w, prices=prices, names={}, universe=list(prices),
                proxy="511880", proxy_price=proxy_price, lot=lot, bp=bp, min_fee=min_fee, hold_cash_in_proxy=hold)


# ------------------------------------------------------------------------------------------------ lot rounding
def test_lot_rounding_whole_lots():
    assert floor_shares(10_000, 141.0, 100) == 0.0                 # one lot costs 14,100: cannot afford
    assert floor_shares(14_100, 141.0, 100) == 100.0               # exactly one lot
    assert floor_shares(14_099.99, 141.0, 100) == 0.0
    assert floor_shares(33_000, 141.0, 100) == 200.0
    assert floor_shares(33_000, 4.236, 100) == 7700.0
    assert floor_shares(0, 4.0, 100) == 0.0 and floor_shares(-5, 4.0, 100) == 0.0


def test_fractional_rounding_floors_to_four_decimals():
    assert floor_shares(1000, 709.8, 0) == pytest.approx(1.4088)   # 1.408848... floored, never rounded up
    assert floor_shares(1000, 709.8, 0) * 709.8 <= 1000
    assert floor_shares(1000, 3.0, 0) == pytest.approx(333.3333)


def test_plan_buys_only_whole_lots_and_never_exceeds_capital():
    p = _plan(100_000, {}, {"510300": 0.5, "511010": 0.5}, {"510300": 4.236, "511010": 140.972}, hold=False)
    for r in p["rows"]:
        assert r["tgt_shares"] % 100 == 0 and r["cur_shares"] % 100 == 0
    assert sum(r["tgt_shares"] * r["price"] for r in p["rows"]) + p["fees"] <= 100_000 + 1e-6
    assert p["cash_after"] >= 0


def test_target_below_one_lot_is_flagged_and_goes_to_proxy():
    p = _plan(100_000, {}, {"511010": 0.1, "510300": 0.9}, {"510300": 4.236, "511010": 140.972}, hold=False)
    r = {x["symbol"]: x for x in p["rows"]}
    assert r["511010"]["tgt_shares"] == 0 and any("不足一手" in n for n in p["notes"])


# ------------------------------------------------------------------------------------------------ fees
def test_commission_cn_minimum_and_rate():
    assert commission(1_000, 2.5, 5.0) == 5.0                      # 0.25 -> minimum 5
    assert commission(20_000, 2.5, 5.0) == 5.0                     # exactly 5
    assert commission(98_680, 2.5, 5.0) == pytest.approx(24.67)    # 2.5 bp
    assert commission(0, 2.5, 5.0) == 0.0                          # no trade, no fee
    assert commission(10_000, 0.0, 0.0) == 0.0                     # US


def test_plan_fee_is_per_order_and_summed():
    p = _plan(50_000, {}, {"510300": 0.2, "510050": 0.2, "511010": 0.6},
              {"510300": 4.0, "510050": 3.0, "511010": 140.0}, hold=False)
    fee_rows = [r for r in p["rows"] if r["side"] != "持有"]
    assert p["fees"] == pytest.approx(sum(commission(r["amount"], 2.5, 5.0) for r in fee_rows))
    assert all(r["fee"] >= 5.0 for r in fee_rows)                  # every executed order pays at least 5 CNY
    assert all(r["fee"] == 0.0 for r in p["rows"] if r["side"] == "持有")


def test_us_plan_has_no_fees_and_allows_fractions():
    p = plan(capital=10_000, cur_w={}, tgt_w={"QQQ": 0.5, "EFA": 0.5}, prices={"QQQ": 709.8, "EFA": 103.55}, names={},
             universe=["QQQ", "EFA"], proxy="SGOV", proxy_price=100.59, lot=0, bp=0.0, min_fee=0.0,
             hold_cash_in_proxy=False)
    assert p["fees"] == 0.0
    q = {r["symbol"]: r for r in p["rows"]}
    assert q["QQQ"]["tgt_shares"] == pytest.approx(7.0443, abs=1e-4)
    assert 0 <= p["cash_after"] < 0.01 * 709.8 / 100         # fractional leftover is tiny


# ------------------------------------------------------------------------------------------------ leftover routing
def test_cash_weight_and_rounding_leftover_go_to_proxy_lots():
    p = _plan(100_000, {}, {"511010": 0.4, "CASH": 0.6}, {"511010": 140.972}, hold=False)
    r = {x["symbol"]: x for x in p["rows"]}
    assert r["511880"]["is_proxy"] and r["511880"]["side"] == "买入"
    assert r["511880"]["tgt_shares"] % 100 == 0 and r["511880"]["tgt_shares"] >= 500   # ~60k / 100.9
    # what is left is less than one proxy lot (otherwise it would have been routed too)
    assert 0 <= p["cash_after"] < 100.9 * 100


def test_leftover_below_one_proxy_lot_stays_as_cash():
    p = _plan(100_000, {}, {"511010": 1.0}, {"511010": 140.972}, hold=False)
    r = {x["symbol"]: x for x in p["rows"]}
    assert r["511010"]["tgt_shares"] == 700 and r["511880"]["tgt_shares"] == 0     # 1,320 left < one lot (10,090)
    assert p["cash_after"] == pytest.approx(100_000 - 700 * 140.972 - p["fees"])


def test_fees_never_push_cash_negative():
    # full allocation of an exactly divisible amount: the 5 CNY fee must come out of a trimmed position
    p = _plan(10_000, {}, {"510300": 1.0}, {"510300": 1.0})
    assert p["cash_after"] >= -1e-9
    assert p["rows"][0]["tgt_shares"] < 10_000


def test_cash_accounting_identity():
    cur = {"510300": 0.3, "511010": 0.4}                            # remaining 0.3 sits in the proxy
    p = _plan(100_000, cur, {"510300": 0.1, "518880": 0.5, "511010": 0.2},
              {"510300": 4.236, "518880": 8.554, "511010": 140.972})
    flows = sum(r["amount"] for r in p["rows"] if r["side"] == "卖出") - sum(r["amount"] for r in p["rows"] if r["side"] == "买入")
    assert p["cash_after"] == pytest.approx(p["cash_before"] + flows - p["fees"])
    value_after = sum(r["tgt_shares"] * r["price"] for r in p["rows"])
    assert p["cash_after"] == pytest.approx(100_000 - value_after - p["fees"])
    assert {r["symbol"]: r["side"] for r in p["rows"]}["510300"] == "卖出"


# ------------------------------------------------------------------------------------------------ account file
def test_load_account_defaults_override_and_bad_capital(tmp_path):
    d = config.load_account(tmp_path / "missing.json")
    assert d["CN"]["capital"] == 100_000 and d["US"]["capital"] == 10_000 and d["CN"]["cash_proxy"] == "511880"
    f = tmp_path / "a.json"
    f.write_text(json.dumps({"CN": {"capital": 250000}, "US": {"capital": -5}, "_comment": "x"}))
    a = config.load_account(f)
    assert a["CN"]["capital"] == 250000 and a["CN"]["lot_size"] == 100
    assert a["US"]["capital"] == 10_000                            # invalid value falls back to the default
    assert config.load_account()["CN"]["capital"] > 0              # the committed dashboard/account.json is valid


# ------------------------------------------------------------------------------------------------ identity
@pytest.fixture(scope="module")
def cn():
    spec = config.cn_spec()
    return spec, cn_data.load_bars(spec.universe), cn_data.load_shibor(), get_xshg_calendar()


def _view(cn, account, now=datetime(2026, 9, 30, 22, tzinfo=UTC), freeze="2024-01-02", **kw):
    spec, bars, shibor, cal = cn
    return compute_market_view(spec, bars, shibor, now, base_cal=cal, freeze_date=freeze, account=account, **kw)


def _strip(v):
    return json.loads(json.dumps({k: x for k, x in v.items() if k != "orders"}, default=str, sort_keys=True))


def test_order_list_never_changes_nav_or_signals(cn):
    off = _view(cn, {"enabled": False})
    assert off["orders"] is None
    on = _view(cn, dict(config.ACCOUNT_DEFAULTS["CN"]))
    big = _view(cn, dict(config.ACCOUNT_DEFAULTS["CN"], capital=5_000_000.0))
    assert on["orders"]["sections"] and big["orders"]["sections"]
    assert _strip(off) == _strip(on) == _strip(big)                # NAV series, holdings, signals, what-if: identical
    assert off["series"]["strat"] == on["series"]["strat"] == big["series"]["strat"]


def test_compute_orders_does_not_mutate_view_or_bars(cn):
    spec, bars, shibor, cal = cn
    v = _view(cn, {"enabled": False})
    trimmed = {s: df[df["Date"] <= v["as_of"]].reset_index(drop=True) for s, df in bars.items()}
    v0, b0 = copy.deepcopy(v), copy.deepcopy(trimmed)
    o = compute_orders(v, trimmed, dict(config.ACCOUNT_DEFAULTS["CN"]))
    assert v == v0
    assert all(trimmed[s].equals(b0[s]) for s in trimmed)
    assert o["disclaimer"] == DISCLAIMER


def test_orders_with_existing_holdings_and_render(cn):
    v = _view(cn, dict(config.ACCOUNT_DEFAULTS["CN"]))
    o = v["orders"]
    assert o["lot"] == 100 and o["cash_proxy"] == "511880"
    for sec in o["sections"]:
        assert sec["cash_after"] >= -1e-9
        for r in sec["rows"]:
            assert r["tgt_shares"] % 100 == 0
    html = render_page({"CN": v}, datetime(2026, 9, 30, 22, tzinfo=UTC))
    assert "调仓清单" in html and DISCLAIMER in html and "不构成投资建议" in html
    html2 = render_page({"CN": {**v, "orders": {"error": "boom"}}}, datetime(2026, 9, 30, 22, tzinfo=UTC))
    assert "调仓清单计算失败" in html2


def test_order_failure_does_not_break_the_view(cn):
    v = _view(cn, dict(config.ACCOUNT_DEFAULTS["CN"], capital=0.0))        # capital 0 -> nothing to buy, no crash
    assert v["status"] == "ok" and v["orders"] is not None


def test_extra_prices_roundtrip_in_snapshot(tmp_path):
    bars = {"X": pd.DataFrame({"Date": ["2026-01-02"], "open": [1.0], "high": [1.0], "low": [1.0], "close": [1.0],
                               "adj_close": [1.0], "adj_open": [1.0], "volume": [1.0]})}
    fr = FetchResult("CN", bars, pd.Series([1.5], index=["2026-01-02"]), {"X": "THS"}, "x", [], "2026-01-02T00:00:00+00:00",
                     {"511880": 100.9})
    d = write_snapshot(tmp_path, "2026-01-02", fr)
    assert load_snapshot(d, "CN").extra_prices == {"511880": 100.9}
