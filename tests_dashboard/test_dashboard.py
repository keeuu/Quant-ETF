"""Tests for the dashboard package. Cached local data only: research_v3/cn_transfer/data and the research_v2 snapshot."""
from __future__ import annotations

import dataclasses
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pytest

from dashboard import config
from dashboard.calendar_hybrid import HybridCalendar
from dashboard.cli import run
from dashboard.fetch import FetchResult, retry
from dashboard.render import NOTE, render_page
from dashboard.safety import sanitize, setup_logging
from dashboard.shadow import (compute_market_view, finished_sessions, known_sessions, next_rebalance,
                              true_month_ends)
from research_v2.calendar import get_trading_calendar, market_as_of_after_close
from research_v2.schedule import build_monthly_matched_schedule, build_weekly_schedule
from research_v2.signals import (compute_matched_monthly_signals, compute_model_signals_for_events,
                                 compute_weekly_trend)
from research_v3.cash_yield import CashYieldCurve, load_irx
from research_v3.cn_transfer import data as cn_data
from research_v3.cn_transfer.calendar_cn import (CN_TZ, build_monthly_matched_schedule_cal,
                                                  build_weekly_schedule_cal, get_xshg_calendar,
                                                  market_as_of_after_close_cal)
from research_v3.cn_transfer.engine_cn import run_backtest_cn
from research_v3.cn_transfer.runner import build_frames, strategy_frames
from research_v3.cn_transfer.signals_cn import ShiborCurve, cap_def_frame
from research_v3.config import V3Config
from research_v3.engine import run_backtest_v3
from research_v3.signals_step3 import make_defensive_provider, reselect3
from research_v3.step2 import _setup
from research_v3.step4 import benchmark_frames

from .conftest import DUMMY_KEY

UTC = timezone.utc


def utc(y, m, d, h=0, mi=0):
    return datetime(y, m, d, h, mi, tzinfo=UTC)


# ------------------------------------------------------------------------------------------------ fixtures
@pytest.fixture(scope="module")
def cn():
    spec = config.cn_spec()
    return spec, cn_data.load_bars(spec.universe), cn_data.load_shibor(), get_xshg_calendar()


@pytest.fixture(scope="module")
def us():
    spec = config.us_spec()
    view, sessions, weekly, monthly = _setup("validation")
    return spec, view, load_irx(), get_trading_calendar()


def _cn_reference(spec, bars, shibor, cal, score_end, freeze, initial=1_000_000.0):
    """The research cn_transfer pipeline (runner functions) up to a true month-end `score_end`."""
    b = {s: df[df["Date"] <= score_end].reset_index(drop=True) for s, df in bars.items()}
    data_start = max(df["Date"].iloc[0] for df in b.values())
    sessions = [x.strftime("%Y-%m-%d") for x in cal.sessions_in_range(data_start, score_end)]
    curve = ShiborCurve(shibor)
    as_of = market_as_of_after_close_cal(cal, score_end, CN_TZ)
    weekly = build_weekly_schedule_cal(cal, sessions, as_of)
    monthly = build_monthly_matched_schedule_cal(cal, sessions, as_of, weekly)
    wf, mf = build_frames(None, b, spec.universe, spec.groups, sessions, weekly, monthly, curve, spec.def_symbol)
    defensive = make_defensive_provider(b, sessions, curve, symbol=spec.def_symbol, months=3)
    capdef = cap_def_frame(mf, "CAP_DEF_M", universe=spec.universe, cap_groups=spec.groups, defensive=defensive,
                           def_symbol=spec.def_symbol, top_n=spec.top_n)
    return b, sessions, curve, mf, capdef


# ------------------------------------------------------------------------------------------------ replay identity
def test_replay_identity_cn(cn):
    spec, bars, shibor, cal = cn
    freeze = "2024-01-02"
    now = utc(2026, 9, 30, 22)                      # 2026-09-30 is a true month end and a session
    v = compute_market_view(spec, bars, shibor, now, base_cal=cal, freeze_date=freeze)
    assert v["as_of"] == "2026-09-30" and not v["window"]["empty"]
    b, sessions, curve, mf, capdef = _cn_reference(spec, bars, shibor, cal, "2026-09-30", freeze)
    first = next(r for r in capdef.rows if r["execution_session"] >= freeze)
    score_start = first["execution_session"]
    assert v["window"]["start"] == score_start
    cfg = V3Config(5.0, 5.0, "irx", initial_nav=1_000_000.0)
    frames = strategy_frames(mf, capdef, score_start, "2026-09-30", spec.universe)
    for key, name in (("strat", "CAP_DEF_M"), ("hold", "CSI300_BH"), ("mix", "CN_60_40")):
        nav, _reb, _m = run_backtest_cn(cal, b, score_start, "2026-09-30", frames[name], cfg, curve, spec.universe,
                                        suspension_rules=True)
        assert v["series"][key] == nav["nav"].tolist()
    assert v["series"]["dates"][0] == score_start and v["series"]["strat"][0] > 0


def test_replay_identity_us(us):
    spec, view, irx, cal = us
    freeze = "2019-01-02"
    v = compute_market_view(spec, view.bars, irx, utc(2021, 1, 1, 12), base_cal=cal, freeze_date=freeze)
    assert v["as_of"] == "2020-12-31"
    # research pipeline (research_v2 schedule/signals, research_v3 reselect3 + run_backtest_v3 with its guards)
    _view, sessions, weekly, monthly = _setup("validation")
    wf = compute_model_signals_for_events(view.bars, sessions, weekly, "W_M12_T40", 12, 40)
    mf = compute_matched_monthly_signals(wf, monthly, "M_M12_T40")
    curve = CashYieldCurve(irx)
    defensive = make_defensive_provider(view.bars, sessions, curve, "IEF", 3)
    capdef = reselect3(mf, "M_CAP_DEF_T40", cap_groups=spec.groups, slots="def", sigma=None, defensive=defensive,
                       top_n=3)
    first = next(r for r in capdef.rows if r["execution_session"] >= freeze)
    sub = dataclasses.replace(view, score_start=first["execution_session"], score_end="2020-12-31")
    cfg = V3Config(5.0, 5.0, "irx", initial_nav=1_000_000.0)
    bf = benchmark_frames(sub, mf.rows)
    for key, fr in (("strat", capdef), ("hold", bf["SPY_BH"]), ("mix", bf["60_40"])):
        nav, _r, _m = run_backtest_v3(sub, fr, cfg, curve)
        assert v["series"][key] == nav["nav"].tolist()


# ------------------------------------------------------------------------------------------------ trend distance
def _independent_trend(spec, bars, cal, last):
    sessions = known_sessions(bars)
    sessions = [s for s in sessions if s <= last]
    bb = {s: df[df["Date"] <= last] for s, df in bars.items()}
    hc = HybridCalendar(cal, sessions, spec.tz, spec.open_hm, spec.close_hm)
    weekly = build_weekly_schedule_cal(hc, sessions, market_as_of_after_close_cal(hc, last, spec.tz))
    out = {}
    for s in spec.universe:
        px = dict(zip(bb[s]["Date"], bb[s]["adj_close"]))
        closes = [px.get(e.signal_session, float("nan")) for e in weekly]
        if weekly[-1].signal_session != last:      # today's close stands in as the current weekly close
            closes.append(px[last])
        ok, sma = compute_weekly_trend(closes[-1], closes, spec.trend_weeks)
        out[s] = (ok, sma, closes[-1] / sma - 1.0)
    return out


@pytest.mark.parametrize("now,last", [(utc(2026, 10, 19, 2), "2026-10-16"),     # Friday: complete week
                                      (utc(2026, 9, 16, 9), "2026-09-16")])      # Wednesday: mid-week
def test_trend_distance_matches_frozen_trend_function(cn, now, last):
    spec, bars, shibor, cal = cn
    v = compute_market_view(spec, bars, shibor, now, base_cal=cal, freeze_date="2026-10-19")
    assert v["as_of"] == last
    ref = _independent_trend(spec, bars, cal, last)
    assert [t["symbol"] for t in v["trend"]] == list(spec.universe)
    for t in v["trend"]:
        ok, sma, dist = ref[t["symbol"]]
        assert t["passes"] == ok and t["sma"] == pytest.approx(sma) and t["dist"] == pytest.approx(dist)
        assert t["passes"] == (t["dist"] > 0)      # strict '>' rule


# ------------------------------------------------------------------------------------------------ what-if
def test_what_if_equals_frozen_signal_cn(cn):
    spec, bars, shibor, cal = cn
    d = "2026-07-31"                                # Friday and month end: the rule's own month-end signal
    v = compute_market_view(spec, bars, shibor, utc(2026, 7, 31, 12), base_cal=cal, freeze_date="2026-10-19")
    assert v["as_of"] == d
    _b, _s, _c, _mf, capdef = _cn_reference(spec, bars, shibor, cal, d, "2026-10-19")
    row = capdef.rows[-1]
    assert row["signal_session"] == d
    wi = v["what_if"]
    for k in list(spec.universe) + ["CASH"]:
        assert wi["weights"][k] == pytest.approx(row["weights"][k])
    assert wi["selected"] == row["selected_symbols"]
    assert {t["symbol"]: t["passes"] for t in wi["trend"]} == row["trend_passes"]
    assert v["reference_target"]["signal_session"] == d      # same signal appears as the official reference


def test_what_if_equals_frozen_signal_us(us):
    spec, view, irx, cal = us
    d = "2020-10-30"                                # Friday and last session of October 2020
    v = compute_market_view(spec, view.bars, irx, utc(2020, 10, 30, 22), base_cal=cal, freeze_date="2021-06-01")
    assert v["as_of"] == d
    sessions = [s for s in _setup("validation")[1] if s <= d]
    as_of = market_as_of_after_close(d)
    weekly = build_weekly_schedule(sessions, as_of=as_of)
    monthly = build_monthly_matched_schedule(sessions, as_of=as_of, weekly_events=weekly)
    wf = compute_model_signals_for_events(view.bars, sessions, weekly, "W", 12, 40)
    mf = compute_matched_monthly_signals(wf, monthly, "M")
    curve = CashYieldCurve(irx)
    defensive = make_defensive_provider(view.bars, sessions, curve, "IEF", 3)
    row = reselect3(mf, "X", cap_groups=spec.groups, slots="def", sigma=None, defensive=defensive, top_n=3).rows[-1]
    assert row["signal_session"] == d
    for k in list(spec.universe) + ["CASH"]:
        assert v["what_if"]["weights"][k] == pytest.approx(row["weights"][k])
    assert v["what_if"]["selected"] == row["selected_symbols"]


def test_what_if_marks_changes_against_current_holdings(cn):
    spec, bars, shibor, cal = cn
    v = compute_market_view(spec, bars, shibor, utc(2026, 10, 19, 2), base_cal=cal, freeze_date="2025-06-03")
    wi = v["what_if"]
    assert "当前目标组合" in wi["basis_label"]
    assert isinstance(wi["changed"], bool)
    cur = {r["symbol"]: r["target_weight"] for r in v["holdings"]["rows"]}
    for c in wi["changes"]:
        assert c["from"] == pytest.approx(cur.get(c["symbol"], 0.0) if c["symbol"] != "CASH" else c["from"])


# ------------------------------------------------------------------------------------------------ holdings / window
def test_empty_window_before_first_execution(cn):
    spec, bars, shibor, cal = cn
    v = compute_market_view(spec, bars, shibor, utc(2026, 10, 19, 2), base_cal=cal, freeze_date="2026-10-19")
    assert v["window"]["empty"] and v["window"]["n_sessions"] == 0 and v["series"] is None and v["holdings"] is None
    assert v["reference_target"]["pre_freeze"] is True
    nr = v["next_rebalance"]
    assert (nr["signal_session"], nr["execution_session"], nr["estimated"]) == ("2026-10-30", "2026-11-02", False)
    assert nr["sessions_left"] == 11 and nr["days_left"] == 14


def test_partial_session_bar_is_dropped(cn):
    spec, bars, shibor, cal = cn
    extra = {s: pd.concat([df, df.iloc[[-1]].assign(Date="2026-10-19")], ignore_index=True) for s, df in bars.items()}
    assert known_sessions(extra)[-1] == "2026-10-19"
    assert finished_sessions(spec, cal, known_sessions(extra), utc(2026, 10, 19, 2))[-1] == "2026-10-16"   # 10:00 CST
    assert finished_sessions(spec, cal, known_sessions(extra), utc(2026, 10, 19, 8))[-1] == "2026-10-19"   # 16:00 CST
    v = compute_market_view(spec, extra, shibor, utc(2026, 10, 19, 2), base_cal=cal, freeze_date="2026-10-19")
    assert v["as_of"] == "2026-10-16"


def test_mid_month_event_is_not_a_month_end(cn):
    spec, bars, shibor, cal = cn
    sessions = [s for s in known_sessions(bars) if s <= "2026-10-16"]
    hc = HybridCalendar(cal, sessions, spec.tz, spec.open_hm, spec.close_hm)
    as_of = market_as_of_after_close_cal(hc, "2026-10-16", CN_TZ)
    weekly = build_weekly_schedule_cal(hc, sessions, as_of)
    raw = build_monthly_matched_schedule_cal(hc, sessions, as_of, weekly)
    assert raw[-1].signal_session == "2026-10-16"             # the research builder treats it as a month end
    assert true_month_ends(hc, raw)[-1].signal_session == "2026-09-30"


def test_holdings_consistent_with_ledger(us):
    spec, view, irx, cal = us
    # first post-freeze execution day itself: current weights must be the targets (up to one day of drift)
    v = compute_market_view(spec, view.bars, irx, utc(2021, 1, 1, 12), base_cal=cal, freeze_date="2020-06-01")
    h = v["holdings"]
    assert h is not None
    tot = sum(r["weight"] for r in h["rows"]) + h["cash_weight"]
    assert tot == pytest.approx(1.0, abs=1e-9)
    for r in h["rows"]:
        assert r["entry_date"] <= h["rebalance_date"] and r["weight"] > 0
    assert v["strategy"]["nav"] == pytest.approx(v["series"]["strat"][-1])
    assert v["strategy"]["drawdown"] <= 0 and v["strategy"]["max_drawdown"] <= v["strategy"]["drawdown"]


# ------------------------------------------------------------------------------------------------ calendar
def test_calendar_beyond_xshg_coverage_is_estimated(cn):
    spec, bars, shibor, cal = cn
    sessions = [s for s in known_sessions(bars)]
    hc = HybridCalendar(cal, sessions, spec.tz, spec.open_hm, spec.close_hm)
    assert not hc.covered("2027-02-01")
    # pretend it is mid-January 2027: past sessions are the bars (weekday-only here), future estimated
    fake = [d.strftime("%Y-%m-%d") for d in pd.bdate_range("2026-12-01", "2027-01-15")]
    hc2 = HybridCalendar(cal, fake, spec.tz, spec.open_hm, spec.close_hm)
    nr = next_rebalance(hc2, fake, [], pd.Timestamp("2027-01-16", tz="Asia/Shanghai"))
    assert nr["estimated"] is True
    assert (nr["signal_session"], nr["execution_session"]) == ("2027-01-29", "2027-02-01")
    # known sessions win over the calendar for past dates (a bar-less weekday is not a session)
    assert not hc2.is_session("2026-12-31") or "2026-12-31" in fake


# ------------------------------------------------------------------------------------------------ rendering
def _no_external(html: str):
    low = html.lower()
    assert "<script" not in low and "@import" not in low and "url(" not in low
    assert "src=" not in low and "href=" not in low and "http://" not in low and "https://" not in low


def test_render_empty_window(cn):
    spec, bars, shibor, cal = cn
    v = compute_market_view(spec, bars, shibor, utc(2026, 10, 19, 2), base_cal=cal, freeze_date="2026-10-19",
                            meta={"sources": {s: "THS" for s in spec.universe}, "rate_source": "SHIBOR 3月"})
    html = render_page({"CN": v}, utc(2026, 10, 19, 2))
    assert "前瞻期刚开始，尚无足够数据" in html and NOTE in html and "Asia/Shanghai" in html
    assert "2026-10-19 10:00" in html and "数据截至 2026-10-16" in html
    assert "距 40 周趋势线" in html and "月末预览" in html and "下次调仓" in html and "2026-11-02" in html
    assert "prefers-color-scheme:dark" in html and "viewport" in html
    assert "<svg" in html          # trend bar chart is present even with an empty window
    _no_external(html)


def test_render_populated_and_failed_market(cn, us):
    spec, bars, shibor, cal = cn
    v = compute_market_view(spec, bars, shibor, utc(2026, 10, 19, 2), base_cal=cal, freeze_date="2025-06-03")
    err = {"key": "US", "title": "美股", "status": "error", "message": "RuntimeError: 下载失败", "last_success": None}
    html = render_page({"US": err, "CN": v}, utc(2026, 10, 19, 2), {"US": "2026-10-15"})
    assert "该市场数据获取或计算失败" in html and "下载失败" in html and "最后一次成功：2026-10-15" in html
    assert "净值对比" in html and "polyline" in html and "当前持仓" in html
    _no_external(html)
    html2 = render_page({"US": err, "CN": dict(err, key="CN", title="A股")}, utc(2026, 10, 19, 2))
    assert html2.count("该市场数据获取或计算失败") == 2 and "无记录" in html2


# ------------------------------------------------------------------------------------------------ key safety / cli
def _cn_fetch(cn):
    spec, bars, shibor, cal = cn

    def f(_spec, now):
        return FetchResult("CN", bars, shibor, {s: "THS" for s in spec.universe}, "SHIBOR 3月",
                           ["note"], now.isoformat())
    return f


def _boom(_spec, now):
    raise RuntimeError(f"upstream refused request with header X-api-key: {DUMMY_KEY}")


def test_key_never_in_html_log_or_files(cn, tmp_path):
    spec, bars, shibor, cal = cn
    logger = setup_logging(tmp_path / "logs" / "dashboard.log")
    logger.info("explicit leak attempt %s", DUMMY_KEY)
    out, data = tmp_path / "out", tmp_path / "data"
    markets = {"US": (config.us_spec, _boom, get_trading_calendar), "CN": (config.cn_spec, _cn_fetch(cn), lambda: cal)}
    rc = run(now_utc=utc(2026, 10, 19, 2), output_dir=out, data_root=data, markets=markets,
             freeze_date="2026-10-19")
    for h in logging.getLogger("dashboard").handlers:
        h.flush()
    assert rc == 0                                   # one market failed, the other rendered
    html = (out / "index.html").read_text()
    assert "该市场数据获取或计算失败" in html and "前瞻期刚开始" in html
    assert (out / "archive" / "2026-10-19.html").is_file()
    assert json.loads((data / "last_success.json").read_text()) == {"CN": "2026-10-16"}
    log = (tmp_path / "logs" / "dashboard.log").read_text()
    assert "US FAILED" in log
    for p in [out / "index.html", tmp_path / "logs" / "dashboard.log", *data.rglob("*")]:
        if p.is_file():
            assert DUMMY_KEY not in p.read_text(errors="ignore"), p
    assert sanitize(f"a {DUMMY_KEY} b sk-ANOTHERSECRET12345") == "a *** b ***"


def test_exit_code_nonzero_only_if_both_fail(cn, tmp_path):
    markets = {"US": (config.us_spec, _boom, get_trading_calendar), "CN": (config.cn_spec, _boom, lambda: cn[3])}
    rc = run(now_utc=utc(2026, 10, 19, 2), output_dir=tmp_path / "o", data_root=tmp_path / "d", markets=markets,
             freeze_date="2026-10-19")
    assert rc == 1
    html = (tmp_path / "o" / "index.html").read_text()
    assert html.count("该市场数据获取或计算失败") == 2 and DUMMY_KEY not in html


def test_snapshot_roundtrip_reproduces_page(cn, tmp_path):
    spec, bars, shibor, cal = cn
    markets = {"CN": (config.cn_spec, _cn_fetch(cn), lambda: cal)}
    rc = run(now_utc=utc(2026, 10, 19, 2), output_dir=tmp_path / "o1", data_root=tmp_path / "d", markets=markets,
             freeze_date="2026-10-19")
    assert rc == 0
    man = json.loads((tmp_path / "d" / "2026-10-19" / "manifest.json").read_text())
    assert all(len(m["sha256"]) == 64 for m in man["markets"]["CN"]["files"].values())
    rc = run(now_utc=utc(2026, 10, 20, 2), output_dir=tmp_path / "o2", data_root=tmp_path / "d2",
             snapshot_dir=tmp_path / "d" / "2026-10-19", markets=markets, freeze_date="2026-10-19")
    assert rc == 0
    a, b = (tmp_path / "o1" / "index.html").read_text(), (tmp_path / "o2" / "index.html").read_text()
    # same data and same data-time => same body (only the generation timestamp may differ)
    assert a.split("</header>")[1] == b.split("</header>")[1]
    # tampering is detected
    (tmp_path / "d" / "2026-10-19" / "CN" / "510300.csv").write_text("Date\n")
    with pytest.raises(Exception):
        from dashboard.fetch import load_snapshot
        load_snapshot(tmp_path / "d" / "2026-10-19", "CN")


def test_retry_gives_up_after_three_attempts(monkeypatch):
    import dashboard.fetch as F
    monkeypatch.setattr(F.time, "sleep", lambda s: None)
    calls = []

    def bad():
        calls.append(1)
        raise ValueError(f"token {DUMMY_KEY}")
    with pytest.raises(F.FetchError) as ei:
        retry(bad, tries=3, what="x")
    assert len(calls) == 3 and DUMMY_KEY not in str(ei.value)
