from __future__ import annotations

import json
import logging
from copy import deepcopy
from datetime import datetime, timezone

import pytest

from paper_exec import executor as ex
from paper_exec import journal as jr
from paper_exec.broker import (BASE_URL, PaperBroker, SecurityError, check_request)
from paper_exec.planner import client_order_id, compute_plan

from .conftest import KEY, SECRET, FakeAlpaca

UNIV = ("SPY", "QQQ", "IWM", "EFA", "EEM", "GLD", "TLT", "IEF")
CFG = {"enabled": True, "min_order_usd": 1.0, "max_minutes_after_open": 90, "poll_seconds": 0,
       "poll_interval_seconds": 0, "cash_buffer": 0.002}
W = {"SPY": 0.4, "GLD": 0.3, "CASH": 0.3}


def target(exec_day="2026-11-02", sig="2026-10-30", weights=None):
    t = {"as_of": sig, "universe": list(UNIV), "next_rebalance": {},
         "official": {"signal_session": sig, "execution_session": exec_day, "weights": weights or W,
                      "status": "PENDING", "pre_freeze": False},
         "what_if": {"signal_session": sig, "execution_session_if": exec_day, "weights": weights or W}}
    return lambda now=None: t


def go(fake, tmp_path, cfg=None, submit=True, tp=None, notes=None):
    notes = notes if notes is not None else []
    code, out = ex.run_cmd(submit=submit, cfg=cfg or CFG, broker_factory=lambda allow_submit: PaperBroker(
        allow_submit=allow_submit, http=fake), target_provider=tp or target(),
        notifier=lambda *a: notes.append(a), jpath=tmp_path / "j.jsonl", universe=UNIV, proxy="SGOV",
        sleep=lambda s: None)
    return code, "\n".join(out)


# ---------------------------------------------------------------------------------------------- host whitelist
@pytest.mark.parametrize("url", [
    "https://api.alpaca.markets/v2/account",                    # live host
    "https://data.alpaca.markets/v2/account",
    "https://paper-api.alpaca.markets.evil.com/v2/account",
    "https://evil.com/paper-api.alpaca.markets/v2/account",
    "https://paper-api.alpaca.markets@evil.com/v2/account",
    "https://user:pw@paper-api.alpaca.markets/v2/account",
    "https://paper-api.alpaca.markets:8443/v2/account",
    "http://paper-api.alpaca.markets/v2/account",
])
def test_host_whitelist_rejects_everything_but_paper(url):
    with pytest.raises(SecurityError):
        check_request("GET", url)
    with pytest.raises(SecurityError):
        check_request("POST", url.replace("/v2/account", "/v2/orders"), allow_submit=True)


def test_only_known_paths_and_methods():
    check_request("GET", BASE_URL + "/v2/account")
    for m in ("DELETE", "PATCH", "PUT"):
        with pytest.raises(SecurityError):
            check_request(m, BASE_URL + "/v2/orders", allow_submit=True)
    with pytest.raises(SecurityError):
        check_request("GET", BASE_URL + "/v2/account/configurations/evil")
    with pytest.raises(SecurityError):
        check_request("POST", BASE_URL + "/v2/orders")                 # allow_submit False
    with pytest.raises(SecurityError):
        check_request("POST", BASE_URL + "/v2/account", allow_submit=True)


def test_live_base_url_env_is_ignored(monkeypatch):
    monkeypatch.setenv("ALPACA_BASE_URL", "https://api.alpaca.markets")
    fake = FakeAlpaca()
    PaperBroker(http=fake).get_account()
    assert fake.calls[0][1].startswith("https://paper-api.alpaca.markets/")


def test_broker_without_authorisation_cannot_post():
    b = PaperBroker(allow_submit=False, http=FakeAlpaca())
    with pytest.raises(SecurityError):
        b.submit_market_order(symbol="SPY", side="buy", client_order_id="x", notional=10)


# ---------------------------------------------------------------------------------------------- switches
def test_disabled_config_never_submits(tmp_path):
    fake = FakeAlpaca()
    code, text = go(fake, tmp_path, cfg={**CFG, "enabled": False}, submit=True)
    assert code == 0 and "演练" in text and fake.posts() == []
    assert not any(e["event"] == "plan" for e in jr.read(tmp_path / "j.jsonl"))


def test_missing_submit_flag_never_submits(tmp_path):
    fake = FakeAlpaca()
    code, text = go(fake, tmp_path, submit=False)
    assert code == 0 and "演练" in text and "SPY" in text and fake.posts() == []


def test_enabled_must_be_literal_true(tmp_path):
    p = tmp_path / "c.json"
    p.write_text(json.dumps({"enabled": "true"}))
    assert ex.load_config(p)["enabled"] is False
    assert ex.load_config(tmp_path / "missing.json")["enabled"] is False
    assert json.loads((ex.CONFIG_PATH).read_text())["enabled"] is False        # shipped default


# ---------------------------------------------------------------------------------------------- no-op days
def test_not_execution_day_midmonth(tmp_path):
    fake = FakeAlpaca(now="2026-10-29T09:40:00-04:00", days=["2026-10-27", "2026-10-28", "2026-10-29"])
    code, text = go(fake, tmp_path)
    assert code == 0 and "今天不是执行日" in text and fake.posts() == []


def test_not_execution_day_signal_mismatch(tmp_path):
    fake = FakeAlpaca()
    code, text = go(fake, tmp_path, tp=target(exec_day="2026-12-01", sig="2026-11-30"))
    assert code == 0 and "今天不是执行日" in text and fake.posts() == []


def test_holiday_is_noop(tmp_path):
    fake = FakeAlpaca(days=["2026-10-29", "2026-10-30"])
    code, text = go(fake, tmp_path)
    assert "今天不是执行日" in text and fake.posts() == []


def test_market_closed_is_noop(tmp_path):
    fake = FakeAlpaca(is_open=False, now="2026-11-02T08:00:00-05:00")
    code, text = go(fake, tmp_path)
    assert code == 0 and "未开盘" in text and fake.posts() == []


def test_outside_window_is_noop(tmp_path):
    fake = FakeAlpaca(now="2026-11-02T13:00:00-05:00")
    code, text = go(fake, tmp_path)
    assert "超出执行窗口" in text and fake.posts() == []


# ---------------------------------------------------------------------------------------------- planner
def pos(**kw):
    return {s: {"qty": v / 100, "market_value": v, "price": 100.0, "side": "long"} for s, v in kw.items()}


def plan_of(positions, equity=100000.0, weights=W, frac=None, min_usd=1.0, prices=None):
    return compute_plan(signal_date="2026-10-30", weights=weights, universe=UNIV, proxy="SGOV", equity=equity,
                        positions=positions, fractionable=frac or {s: True for s in (*UNIV, "SGOV")},
                        min_usd=min_usd, prices=prices)


def test_delta_and_sells_before_buys():
    p = plan_of(pos(EEM=20000, SPY=10000, SGOV=70000))
    sides = [o["side"] for o in p["orders"]]
    assert sides == sorted(sides, key=lambda s: s != "sell")
    by = {o["symbol"]: o for o in p["orders"]}
    assert by["EEM"]["side"] == "sell" and by["EEM"]["qty"] == "200.0"               # full liquidation by qty
    assert by["SPY"]["side"] == "buy" and by["SPY"]["notional"] == 30000.0           # 40000 - 10000
    assert by["GLD"]["notional"] == 30000.0
    assert by["SGOV"]["side"] == "sell" and by["SGOV"]["notional"] == 40000.0        # 70000 -> 30000
    assert by["SGOV"]["client_order_id"] == client_order_id("2026-10-30", "SGOV", "sell") == "capdef-2026-10-30-SGOV-sell"
    assert p["orders"][0]["side"] == "sell" and p["orders"][-1]["side"] == "buy"


def test_one_dollar_threshold():
    p = plan_of(pos(SPY=39999.5, GLD=30000.4, SGOV=29999.9))
    assert p["orders"] == []
    p = plan_of(pos(SPY=39998.0, GLD=30000.0, SGOV=30002.0))
    assert [(o["symbol"], o["side"]) for o in p["orders"]] == [("SGOV", "sell"), ("SPY", "buy")]


def test_non_strategy_holdings_untouched_and_excluded_from_base():
    p = plan_of(pos(FOO=10000, SGOV=90000), equity=100000.0)
    assert "FOO" not in {o["symbol"] for o in p["orders"]}
    assert p["non_strategy"] == {"FOO": 10000.0} and p["strategy_base"] == 90000.0
    by = {o["symbol"]: o for o in p["orders"]}
    assert by["SPY"]["notional"] == 36000.0


def test_non_fractionable_uses_whole_shares():
    frac = {s: True for s in (*UNIV, "SGOV")}
    frac["GLD"] = False
    p = plan_of({}, frac=frac, prices={"GLD": 250.0})
    g = next(o for o in p["orders"] if o["symbol"] == "GLD")
    assert g["qty"] == "120" and "notional" not in g
    p = plan_of({}, frac=frac)                                                # no price -> skipped, reported
    assert all(o["symbol"] != "GLD" for o in p["orders"]) and p["notes"]


# ---------------------------------------------------------------------------------------------- end to end + idempotency
def test_full_run_sells_first_then_buys_and_notifies(tmp_path):
    fake = FakeAlpaca(equity=100000.0, cash=75000.0, positions={"EEM": 20000.0, "FOO": 5000.0})
    notes = []
    code, text = go(fake, tmp_path, notes=notes)
    assert code == 0, text
    posts = fake.posts()
    assert [p[2]["side"] for p in posts][0] == "sell" and posts[0][2]["symbol"] == "EEM"
    assert {p[2]["symbol"] for p in posts} == {"EEM", "SPY", "GLD", "SGOV"}      # never FOO
    assert all(p[2]["type"] == "market" and p[2]["time_in_force"] == "day" for p in posts)
    assert jr.state("2026-10-30", tmp_path / "j.jsonl")["complete"]
    assert notes and "美股模拟账户已按信号调仓" in notes[0][2]
    assert "非策略持仓" in text


def test_rerun_same_signal_sends_nothing(tmp_path):
    fake = FakeAlpaca()
    assert go(fake, tmp_path)[0] == 0
    n = len(fake.posts())
    assert n == 3
    code, text = go(fake, tmp_path)
    assert code == 0 and "幂等" in text and len(fake.posts()) == n


def test_rerun_without_journal_still_does_not_resend_existing_ids(tmp_path):
    fake = FakeAlpaca()
    go(fake, tmp_path)
    n = len(fake.posts())
    (tmp_path / "j.jsonl").unlink()                                   # journal lost: broker is the second line of defence
    go(fake, tmp_path)
    assert len(fake.posts()) == n


def test_partial_failure_resumes_only_missing_ids(tmp_path):
    fake = FakeAlpaca(equity=100000.0, cash=80000.0, positions={"EEM": 20000.0})
    fake.reject.add("capdef-2026-10-30-GLD-buy")
    code, text = go(fake, tmp_path)
    assert code == 2
    assert not jr.state("2026-10-30", tmp_path / "j.jsonl")["complete"]
    sent_first = {p[2]["client_order_id"] for p in fake.posts()}
    assert "capdef-2026-10-30-EEM-sell" in sent_first
    fake.calls.clear()
    code, text = go(fake, tmp_path)
    assert code == 0 and "续做" in text
    assert [p[2]["client_order_id"] for p in fake.posts()] == ["capdef-2026-10-30-GLD-buy"]
    fake.calls.clear()
    assert go(fake, tmp_path)[0] == 0 and fake.posts() == []


def test_sell_not_filled_holds_back_buys_then_resumes(tmp_path):
    fake = FakeAlpaca(equity=100000.0, cash=80000.0, positions={"EEM": 20000.0})
    orig = fake.__call__

    state = {"slow": True}

    def http(method, url, headers, params, body, timeout):
        s, b = orig(method, url, headers, params, body, timeout)
        if state["slow"] and isinstance(b, dict) and b.get("side") == "sell":
            b = dict(b, status="accepted")
            fake.orders[b["client_order_id"]] = b
        return s, b
    http.calls = fake.calls
    code, text = ex.run_cmd(submit=True, cfg=CFG, broker_factory=lambda allow_submit: PaperBroker(allow_submit=allow_submit, http=http),
                            target_provider=target(), notifier=lambda *a: None, jpath=tmp_path / "j.jsonl",
                            universe=UNIV, proxy="SGOV", sleep=lambda s: None)
    assert code == 2 and [p[2]["side"] for p in fake.posts()] == ["sell"]
    state["slow"] = False
    fake.orders["capdef-2026-10-30-EEM-sell"]["status"] = "filled"
    fake.calls.clear()
    code, _ = go(fake, tmp_path)
    assert code == 0 and {p[2]["side"] for p in fake.posts()} == {"buy"}


# ---------------------------------------------------------------------------------------------- keys
def test_keys_never_in_logs_or_journal(tmp_path):
    ex.setup_logging(tmp_path / "pe.log")
    ex.log.info("leak attempt %s %s", KEY, SECRET)
    fake = FakeAlpaca()
    fake.reject.add("capdef-2026-10-30-GLD-buy")                     # error body contains the key
    go(fake, tmp_path)
    jr.append({"signal_date": "x", "event": "leak", "msg": f"{KEY} {SECRET}"}, tmp_path / "j.jsonl")
    for h in ex.log.handlers:
        h.flush()
    blob = (tmp_path / "pe.log").read_text() + (tmp_path / "j.jsonl").read_text()
    assert KEY not in blob and SECRET not in blob and "ACCT-NUMBER-SECRET" not in blob
    assert "***" in blob
    assert repr(PaperBroker()).count(KEY) == 0
    logging.getLogger("paper_exec").handlers.clear()


# ---------------------------------------------------------------------------------------------- dashboard
def test_status_and_cli_plumbing(tmp_path):
    from paper_exec.cli import cmd_status
    fake = FakeAlpaca(positions={"FOO": 5000.0, "SPY": 1000.0})
    out = "\n".join(cmd_status(PaperBroker(http=fake), CFG, tmp_path / "j.jsonl", UNIV, "SGOV"))
    assert "非策略持仓" in out and "ACCT-NUMBER" not in out and fake.posts() == []


def test_dashboard_identity_with_paper_section(tmp_path):
    from dashboard import config
    from dashboard.cli import run
    from dashboard.fetch import FetchResult
    from dashboard.render import render_page
    from dashboard.shadow import compute_market_view
    from research_v3.cn_transfer import data as cn_data
    from research_v3.cn_transfer.calendar_cn import get_xshg_calendar

    spec = config.cn_spec()
    bars, shibor, cal = cn_data.load_bars(spec.universe), cn_data.load_shibor(), get_xshg_calendar()
    now = datetime(2026, 10, 19, 2, tzinfo=timezone.utc)
    v = compute_market_view(spec, bars, shibor, now, base_cal=cal, freeze_date="2025-06-03")
    v2 = deepcopy(v)
    v2["paper_account"] = {"equity": 100000.0, "cash": 1.0, "buying_power": 1.0, "last_execution": None,
                           "positions": [{"symbol": "SPY", "qty": 1.0, "market_value": 400.0, "weight": 0.4, "strategy": True},
                                         {"symbol": "FOO", "qty": 1.0, "market_value": 50.0, "weight": 0.05, "strategy": False}]}
    h1, h2 = render_page({"CN": v}, now), render_page({"CN": v2}, now)
    assert "模拟账户实际持仓" not in h1 and "模拟账户实际持仓" in h2 and "非策略持仓" in h2
    start = h2.index("<details><summary>模拟账户实际持仓")
    end = h2.index("</details>", start) + len("</details>")
    assert h2[:start] + h2[end:] == h1                     # everything else byte-identical
    assert "<details open><summary>模拟账户实际持仓" not in h2
    v2.pop("paper_account")
    assert v2 == v                                         # NAV, holdings, signals untouched

    def fetch(_s, n):
        return FetchResult("CN", bars, shibor, {s: "THS" for s in spec.universe}, "SHIBOR 3月", [], n.isoformat())
    markets = {"US": (config.cn_spec, fetch, lambda: cal)}      # reuse the cached CN data under the US slot
    calls = []
    kw = dict(now_utc=now, markets=markets, freeze_date="2025-06-03", write_snapshots=False)
    run(output_dir=tmp_path / "a", data_root=tmp_path / "da", **kw)
    run(output_dir=tmp_path / "b", data_root=tmp_path / "db", paper_provider=lambda view: calls.append(1) or {
        "error": "x"}, **kw)
    a, b = (tmp_path / "a" / "index.html").read_text(), (tmp_path / "b" / "index.html").read_text()
    assert calls == [1] and "模拟账户读取失败" in b
    s = b.index("<details><summary>模拟账户实际持仓")
    e = b.index("</details>", s) + len("</details>")
    assert b[:s] + b[e:] == a
    run(output_dir=tmp_path / "c", data_root=tmp_path / "dc", paper_provider=lambda view: 1 / 0, **kw)   # failure is non-fatal
    assert "模拟账户" not in (tmp_path / "c" / "index.html").read_text()
