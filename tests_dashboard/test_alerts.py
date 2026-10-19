"""Alerts: conditions, dedup, batching, escaping, failure isolation, read-only identity, --no-notify. Notifier is mocked."""
from __future__ import annotations

import copy
import json
import re
import subprocess
from datetime import datetime, timezone

import pytest

from dashboard import alerts as A
from dashboard import cli, config
from dashboard.cli import run
from dashboard.render import render_page
from research_v3.cn_transfer.calendar_cn import get_xshg_calendar

from .test_dashboard import _boom, _cn_fetch, cn, utc  # noqa: F401  (cn is a module fixture)

NOW = datetime(2026, 11, 2, 8, tzinfo=timezone.utc)
CFG = A.load_alert_config(path="/nonexistent")          # pure defaults


@pytest.fixture(autouse=True)
def _no_real_osascript(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("real osascript/subprocess call in a test")
    monkeypatch.setattr(A.subprocess, "run", boom)


class Rec:
    """Mock notifier; returns ok."""
    def __init__(self, ok=True):
        self.calls, self.ok = [], ok

    def __call__(self, title, subtitle, body):
        self.calls.append((title, subtitle, body))
        return self.ok


def make_view(key="CN", *, dist=None, held=("511010",), changes=(), sessions_left=10, status="COMPLETE",
              pre_freeze=False, drawdown=0.0, empty=False, orders=None):
    dist = dist or {"511010": 0.05, "510300": -0.2}
    uni = ["510300", "511010"]
    trend = [{"symbol": s, "name": s, "close": 1.0, "sma": 1.0, "dist": dist.get(s), "passes": True,
              "passes_official": True} for s in uni]
    return {
        "key": key, "title": "A股" if key == "CN" else "美股", "status": "ok", "universe": uni,
        "names": {"510300": "沪深300ETF", "511010": "国债ETF"}, "trend": trend,
        "holdings": {"rows": [{"symbol": s, "weight": 1 / len(held)} for s in held]} if held else None,
        "reference_target": {"signal_session": "2026-10-30", "execution_session": "2026-11-02", "status": status,
                             "pre_freeze": pre_freeze, "weights": {"511010": 1.0}},
        "what_if": {"changes": [{"symbol": s, "kind": k, "from": 0, "to": 1} for s, k in changes],
                    "changed": bool(changes)},
        "next_rebalance": {"signal_session": "2026-11-30", "execution_session": "2026-12-01", "estimated": False,
                           "sessions_left": sessions_left, "days_left": sessions_left},
        "window": {"empty": empty}, "strategy": {"drawdown": drawdown}, "orders": orders,
    }


def codes(view, cfg=CFG):
    return sorted(a["code"] for a in A.evaluate(view, A.market_cfg(cfg, view["key"])))


def cfg_with(**kw):
    c = copy.deepcopy(CFG)
    for k, v in kw.items():
        c[k].update(v)
    return c


# ------------------------------------------------------------------------------------------------ conditions
@pytest.mark.parametrize("d,fires", [(0.0199, True), (0.02, True), (0.0201, False), (-0.0199, True), (-0.02, True),
                                     (-0.0201, False), (None, False)])
def test_a1_boundary(d, fires):
    v = make_view(dist={"511010": d})
    assert ("A1" in codes(v)) is fires


def test_a1_threshold_configurable_and_only_held():
    v = make_view(dist={"511010": 0.05, "510300": 0.001})        # 510300 near trend but not held
    assert "A1" not in codes(v)
    assert "A1" in codes(v, cfg_with(A1={"threshold_pct": 5.0}))
    al = [a for a in A.evaluate(v, A.market_cfg(cfg_with(A1={"threshold_pct": 5.0}), "CN")) if a["code"] == "A1"]
    assert [a["asset"] for a in al] == ["511010"]


def test_a1_uses_reference_target_when_not_invested():
    v = make_view(held=None, dist={"511010": 0.01, "510300": 0.0})
    al = [a for a in A.evaluate(v, A.market_cfg(CFG, "CN")) if a["code"] == "A1"]
    assert [a["asset"] for a in al] == ["511010"]                 # target holds only 511010


def test_a2_fires_on_change_only():
    assert "A2" not in codes(make_view(changes=()))
    v = make_view(changes=[("510300", "新增"), ("511010", "清仓")])
    a = [x for x in A.evaluate(v, A.market_cfg(CFG, "CN")) if x["code"] == "A2"][0]
    assert "新增510300沪深300ETF" in a["text"] and "清仓511010国债ETF" in a["text"]


@pytest.mark.parametrize("left,fires", [(1, True), (2, True), (3, False)])
def test_a3_boundary(left, fires):
    assert ("A3" in codes(make_view(sessions_left=left))) is fires


def test_a3_n_configurable():
    assert "A3" in codes(make_view(sessions_left=5), cfg_with(A3={"sessions": 5}))


def test_a4_pending_with_order_summary():
    orders = {"capital": 100000, "currency": "CNY", "fractional": False,
              "sections": [{"kind": "preview", "rows": [{"side": "买入", "symbol": "X", "delta": 1}]},
                           {"kind": "official", "rows": [{"side": "买入", "symbol": "511010", "delta": 700},
                                                         {"side": "卖出", "symbol": "510300", "delta": -300},
                                                         {"side": "持有", "symbol": "518880", "delta": 0}]}]}
    v = make_view(status="PENDING", orders=orders, sessions_left=1)
    al = A.evaluate(v, A.market_cfg(CFG, "CN"))
    a4 = [a for a in al if a["code"] == "A4"][0]
    assert "100,000CNY" in a4["text"] and "买入511010 700份" in a4["text"] and "卖出510300 300份" in a4["text"]
    assert "X" not in a4["text"] and "518880" not in a4["text"]
    assert "A3" not in [a["code"] for a in al]                    # A4 supersedes A3
    assert "A4" not in codes(make_view(status="COMPLETE"))
    assert "A4" not in codes(make_view(status="PENDING", pre_freeze=True))


@pytest.mark.parametrize("dd,fires", [(-0.1499, False), (-0.15, False), (-0.1501, True)])
def test_a6_boundary(dd, fires):
    assert ("A6" in codes(make_view(drawdown=dd))) is fires
    assert "A6" not in codes(make_view(drawdown=-0.5, empty=True))


@pytest.mark.parametrize("streak,fires", [(1, False), (2, True), (3, True)])
def test_a5_boundary(streak, fires):
    assert bool(A.check_a5("CN", streak, {"consecutive_runs": 2}, "x")) is fires


def test_a5_through_process_two_consecutive_failures(tmp_path):
    n, st = Rec(), tmp_path / "s.json"
    err = {"key": "CN", "title": "A股", "status": "error", "message": "RuntimeError: boom"}
    r1 = A.process({"CN": err}, NOW, cfg=CFG, state_path=st, notifier=n)
    assert n.calls == [] and r1["markets"]["CN"]["alerts"] == []
    A.process({"CN": err}, NOW, cfg=CFG, state_path=st, notifier=n)
    assert len(n.calls) == 1 and "连续 2 次失败" in n.calls[0][2]
    A.process({"CN": err}, NOW, cfg=CFG, state_path=st, notifier=n)       # still failing: no repeat
    assert len(n.calls) == 1
    A.process({"CN": make_view()}, NOW, cfg=CFG, state_path=st, notifier=n)  # success resets the streak
    assert json.loads(st.read_text())["markets"]["CN"]["fail_streak"] == 0


def test_disabled_condition_does_not_fire():
    v = make_view(changes=[("510300", "新增")], dist={"511010": 0.0})
    assert codes(v, cfg_with(A1={"enabled": False}, A2={"enabled": False})) == []


def test_per_market_override():
    cfg = copy.deepcopy(CFG)
    cfg["markets"] = {"US": {"A1": {"threshold_pct": 0.5}}}
    v = make_view(dist={"511010": 0.01})
    assert "A1" in codes(v, cfg)
    us = make_view("US", dist={"511010": 0.01})
    assert "A1" not in codes(us, cfg)


def test_load_config_defaults_and_file(tmp_path):
    assert A.load_alert_config(tmp_path / "none.json")["A1"]["threshold_pct"] == 2.0
    p = tmp_path / "a.json"
    p.write_text(json.dumps({"enabled": False, "A1": {"threshold_pct": 3}, "A3": {"sessions": 4}}))
    c = A.load_alert_config(p)
    assert c["enabled"] is False and c["A1"] == {"enabled": True, "threshold_pct": 3} and c["A3"]["sessions"] == 4
    p.write_text("not json")
    assert A.load_alert_config(p)["enabled"] is True
    shipped = A.load_alert_config(config.ALERTS_PATH)
    assert shipped["A1"]["threshold_pct"] == 2.0 and shipped["A3"]["sessions"] == 2 and shipped["A6"]["threshold_pct"] == 15.0


# ------------------------------------------------------------------------------------------------ dedup
def test_dedup_same_state_no_repeat_then_resolve_and_retrigger(tmp_path):
    n, st = Rec(), tmp_path / "s.json"
    near, far = make_view(dist={"511010": 0.01}), make_view(dist={"511010": 0.05})
    A.process({"CN": near}, NOW, cfg=CFG, state_path=st, notifier=n)
    assert len(n.calls) == 1
    A.process({"CN": near}, NOW, cfg=CFG, state_path=st, notifier=n)
    assert len(n.calls) == 1                                       # same state: silent
    A.process({"CN": far}, NOW, cfg=CFG, state_path=st, notifier=n)
    assert len(n.calls) == 1                                       # resolved: silent
    A.process({"CN": near}, NOW, cfg=CFG, state_path=st, notifier=n)
    assert len(n.calls) == 2                                       # came back: notify again


def test_dedup_state_change_side_flip_notifies(tmp_path):
    n, st = Rec(), tmp_path / "s.json"
    A.process({"CN": make_view(dist={"511010": 0.01})}, NOW, cfg=CFG, state_path=st, notifier=n)
    A.process({"CN": make_view(dist={"511010": 0.015})}, NOW, cfg=CFG, state_path=st, notifier=n)
    assert len(n.calls) == 1                                       # still above, value moved: silent
    A.process({"CN": make_view(dist={"511010": -0.01})}, NOW, cfg=CFG, state_path=st, notifier=n)
    assert len(n.calls) == 2 and "下方" in n.calls[1][2]


def test_a2_changes_set_change_renotifies(tmp_path):
    n, st = Rec(), tmp_path / "s.json"
    A.process({"CN": make_view(changes=[("510300", "新增")])}, NOW, cfg=CFG, state_path=st, notifier=n)
    A.process({"CN": make_view(changes=[("510300", "新增")])}, NOW, cfg=CFG, state_path=st, notifier=n)
    assert len(n.calls) == 1
    A.process({"CN": make_view(changes=[("510300", "新增"), ("511010", "清仓")])}, NOW, cfg=CFG, state_path=st,
              notifier=n)
    assert len(n.calls) == 2


def test_a3_a4_fire_once_per_rebalance(tmp_path):
    n, st = Rec(), tmp_path / "s.json"
    for left in (2, 1, 3, 2):                                      # flicker around the threshold
        A.process({"CN": make_view(sessions_left=left)}, NOW, cfg=CFG, state_path=st, notifier=n)
    assert len(n.calls) == 1 and "调仓临近" in n.calls[0][2]
    n2 = Rec()
    for _ in range(3):
        A.process({"CN": make_view(status="PENDING", sessions_left=1)}, NOW, cfg=CFG, state_path=tmp_path / "t.json",
                  notifier=n2)
    A.process({"CN": make_view(status="COMPLETE")}, NOW, cfg=CFG, state_path=tmp_path / "t.json", notifier=n2)
    A.process({"CN": make_view(status="PENDING", sessions_left=1)}, NOW, cfg=CFG, state_path=tmp_path / "t.json",
              notifier=n2)
    assert len(n2.calls) == 1                                      # same signal date: once


def test_failed_delivery_is_retried_next_run(tmp_path):
    st = tmp_path / "s.json"
    bad, good = Rec(ok=False), Rec()
    v = make_view(dist={"511010": 0.01})
    A.process({"CN": v}, NOW, cfg=CFG, state_path=st, notifier=bad)
    assert len(bad.calls) == 1 and json.loads(st.read_text())["markets"]["CN"]["last_notified"] is None
    A.process({"CN": v}, NOW, cfg=CFG, state_path=st, notifier=good)
    assert len(good.calls) == 1
    A.process({"CN": v}, NOW, cfg=CFG, state_path=st, notifier=good)
    assert len(good.calls) == 1


def test_error_market_keeps_previous_alerts(tmp_path):
    n, st = Rec(), tmp_path / "s.json"
    A.process({"CN": make_view(dist={"511010": 0.01})}, NOW, cfg=CFG, state_path=st, notifier=n)
    err = {"key": "CN", "title": "A股", "status": "error", "message": "x"}
    A.process({"CN": err}, NOW, cfg=CFG, state_path=st, notifier=n)
    A.process({"CN": make_view(dist={"511010": 0.01})}, NOW, cfg=CFG, state_path=st, notifier=n)
    assert len(n.calls) == 1                                       # a blip must not re-send the A1


# ------------------------------------------------------------------------------------------------ batching / escaping
def test_one_notification_per_market_batched(tmp_path):
    n = Rec()
    v = make_view(dist={"511010": 0.01}, changes=[("510300", "新增")], sessions_left=1)
    us = make_view("US", dist={"511010": 0.0})
    A.process({"CN": v, "US": us}, NOW, cfg=CFG, state_path=tmp_path / "s.json", notifier=n)
    assert [c[1] for c in n.calls] == ["A股", "美股"]
    cn_body = n.calls[0][2]
    assert all(t in cn_body for t in ("趋势线", "月末预览", "调仓临近")) and n.calls[0][0] == "量化看板"


def test_body_truncated():
    body = A.compose_body([{"code": "A2", "text": "长" * 500}])
    assert len(body) <= A.MAX_BODY and "详见看板" in body


def test_send_notification_uses_argv_and_survives_quotes(monkeypatch):
    seen = {}

    def fake(cmd, **kw):
        seen["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, "", "")
    monkeypatch.setattr(A.subprocess, "run", fake)
    nasty = 'He said "hi" \\ and \'q\' $(rm -rf /) `x` -x'
    assert A.send_notification("量化看板", 'A"股', nasty) is True
    cmd = seen["cmd"]
    assert cmd[0] == "osascript" and nasty in cmd and 'A"股' in cmd
    scripts = [cmd[i + 1] for i, x in enumerate(cmd) if x == "-e"]
    assert not any(nasty in s or "hi" in s for s in scripts)       # payload never spliced into the script
    assert "item 1 of argv" in " ".join(scripts)
    lead = A.send_notification("t", "s", "-evil")
    assert lead and seen["cmd"][seen["cmd"].index("--") + 1] == " -evil"


def test_send_notification_failure_is_swallowed(monkeypatch, caplog):
    monkeypatch.setattr(A.subprocess, "run", lambda c, **k: subprocess.CompletedProcess(c, 1, "", "boom"))
    assert A.send_notification("t", "s", "b") is False
    def raiser(*a, **k):
        raise FileNotFoundError("osascript")
    monkeypatch.setattr(A.subprocess, "run", raiser)
    assert A.send_notification("t", "s", "b") is False


# ------------------------------------------------------------------------------------------------ integration (real data)
def _markets(cn):
    return {"CN": (config.cn_spec, _cn_fetch(cn), lambda: cn[3])}


def _strip_alerts(html):
    return re.sub(r"<!--alerts-->.*?<!--/alerts-->", "", html, flags=re.S)


def test_osascript_failure_does_not_break_rendering(cn, tmp_path, monkeypatch):
    monkeypatch.setattr(A.subprocess, "run", lambda c, **k: subprocess.CompletedProcess(c, 1, "", "denied"))
    out = tmp_path / "o"
    rc = run(now_utc=utc(2026, 10, 19, 2), output_dir=out, data_root=tmp_path / "d", markets=_markets(cn),
             freeze_date="2026-10-19", notify=True, alerts_path=config.ALERTS_PATH)
    assert rc == 0 and "提醒" in (out / "index.html").read_text()


def test_alert_crash_does_not_break_rendering(cn, tmp_path, monkeypatch):
    monkeypatch.setattr(A, "process", lambda *a, **k: 1 / 0)
    out = tmp_path / "o"
    rc = run(now_utc=utc(2026, 10, 19, 2), output_dir=out, data_root=tmp_path / "d", markets=_markets(cn),
             freeze_date="2026-10-19", notify=True)
    html = (out / "index.html").read_text()
    assert rc == 0 and "按规则当前应持有" in html and "<!--alerts-->" not in html


def test_enabling_alerts_leaves_shadow_unchanged(cn, tmp_path):
    """Identity: only the 提醒 card differs between alerts on / off; views are not mutated by evaluation."""
    off = tmp_path / "off.json"
    off.write_text(json.dumps({"enabled": False}))
    n = Rec()
    outs = {}
    for name, ap, notify in (("on", config.ALERTS_PATH, True), ("off", off, True), ("nonotify", config.ALERTS_PATH, False)):
        out = tmp_path / name
        run(now_utc=utc(2026, 10, 19, 2), output_dir=out, data_root=tmp_path / ("d" + name), markets=_markets(cn),
            freeze_date="2026-10-19", notify=notify, notifier=n, alerts_path=ap)
        outs[name] = (out / "index.html").read_text()
    stripped = {k: _strip_alerts(v) for k, v in outs.items()}
    assert stripped["on"] == stripped["off"] == stripped["nonotify"]
    assert "通知已关闭" in outs["off"] and "未发送通知" in outs["nonotify"]
    assert len(n.calls) <= 1                                       # only the "on" run may notify (one market)

    from dashboard.shadow import compute_market_view
    spec, bars, shibor, cal = cn
    from dashboard.fetch import FetchResult
    from research_v3.cn_transfer.calendar_cn import get_xshg_calendar as gc
    view = compute_market_view(spec, bars, shibor, utc(2026, 10, 19, 2), base_cal=gc(), freeze_date="2026-10-19",
                               account=config.load_account()["CN"])
    before = copy.deepcopy(view)
    A.process({"CN": view}, NOW, cfg=CFG, state_path=tmp_path / "x.json", notifier=Rec())
    assert view == before


def test_no_notify_flag(cn, tmp_path, monkeypatch):
    seen = {}

    def fake_run(**kw):
        seen.update(kw)
        return 0
    monkeypatch.setattr(cli, "run", fake_run)
    monkeypatch.setattr(cli, "setup_logging", lambda p: None)
    cli.main(["run", "--no-notify"])
    assert seen["notify"] is False
    cli.main(["run"])
    assert seen["notify"] is True


def test_no_notify_sends_nothing_and_keeps_alert_unspent(tmp_path):
    n, st = Rec(), tmp_path / "s.json"
    v = make_view(dist={"511010": 0.01})
    res = A.process({"CN": v}, NOW, cfg=CFG, state_path=st, notify=False, notifier=n)
    assert n.calls == [] and not st.exists() and res["markets"]["CN"]["alerts"]
    A.process({"CN": v}, NOW, cfg=CFG, state_path=st, notify=True, notifier=n)
    assert len(n.calls) == 1                                       # still fires afterwards


def test_disabled_config_sends_nothing(tmp_path):
    n = Rec()
    A.process({"CN": make_view(dist={"511010": 0.0})}, NOW, cfg=dict(CFG, enabled=False),
              state_path=tmp_path / "s.json", notifier=n)
    assert n.calls == []


def test_render_section_and_last_notified(tmp_path):
    n, st = Rec(), tmp_path / "s.json"
    v = make_view(dist={"511010": 0.01})
    info = A.process({"CN": v}, NOW, cfg=CFG, state_path=st, notifier=n)
    info2 = A.process({"CN": v}, NOW, cfg=CFG, state_path=st, notifier=n)
    assert info2["markets"]["CN"]["last_notified"] == "2026-11-02T08:00:00+00:00"
    from dashboard.render import _alerts_card
    html = _alerts_card(info2, "CN")
    assert "提醒" in html and "A1" in html and "2026-11-02 16:00" in html
    assert "没有需要关注" in _alerts_card({"markets": {"CN": {"alerts": [], "last_notified": None}}}, "CN")


def test_log_redacts_key(tmp_path, caplog):
    from .conftest import DUMMY_KEY
    err = {"key": "CN", "title": "A股", "status": "error", "message": f"header {DUMMY_KEY}"}
    st = tmp_path / "s.json"
    A.process({"CN": err}, NOW, cfg=CFG, state_path=st, notifier=Rec())
    r = A.process({"CN": err}, NOW, cfg=CFG, state_path=st, notifier=Rec())
    assert DUMMY_KEY not in json.dumps(r) and DUMMY_KEY not in st.read_text()
