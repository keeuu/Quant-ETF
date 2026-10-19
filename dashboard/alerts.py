"""Read-only alerts for the dashboard: evaluate conditions on finished market views, deduplicate against a small
state file and send at most one macOS notification per market per run. Never feeds back into signals, NAV or orders."""
from __future__ import annotations

import copy
import json
import logging
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from . import config
from .safety import sanitize

log = logging.getLogger("dashboard")

TITLE = "量化看板"
MAX_BODY = 220
EPS = 1e-12
ONCE_CODES = ("A3", "A4")          # fire at most once per rebalance
LABELS = {"A1": "趋势线", "A2": "月末预览", "A3": "调仓临近", "A4": "新信号", "A5": "数据失败", "A6": "回撤"}

DEFAULTS = {
    "enabled": True,
    "A1": {"enabled": True, "threshold_pct": 2.0},
    "A2": {"enabled": True},
    "A3": {"enabled": True, "sessions": 2},
    "A4": {"enabled": True},
    "A5": {"enabled": True, "consecutive_runs": 2},
    "A6": {"enabled": True, "threshold_pct": 15.0},
    "markets": {},
}


# ------------------------------------------------------------------------------------------------ config
def load_alert_config(path: Path | None = None) -> dict:
    """Defaults overlaid with dashboard/alerts.json (unreadable or malformed file -> defaults)."""
    cfg = copy.deepcopy(DEFAULTS)
    try:
        raw = json.loads(Path(path or config.ALERTS_PATH).read_text(encoding="utf-8"))
    except Exception:
        return cfg
    if not isinstance(raw, dict):
        return cfg
    if isinstance(raw.get("enabled"), bool):
        cfg["enabled"] = raw["enabled"]
    for code in LABELS:
        if isinstance(raw.get(code), dict):
            cfg[code].update({k: v for k, v in raw[code].items() if not k.startswith("_")})
    if isinstance(raw.get("markets"), dict):
        cfg["markets"] = {m: o for m, o in raw["markets"].items() if isinstance(o, dict)}
    return cfg


def market_cfg(cfg: dict, market: str) -> dict:
    out = {c: dict(cfg[c]) for c in LABELS}
    for code, o in (cfg.get("markets", {}).get(market) or {}).items():
        if code in out and isinstance(o, dict):
            out[code].update(o)
    return out


def _num(x, default):
    return float(x) if isinstance(x, (int, float)) and not isinstance(x, bool) else default


# ------------------------------------------------------------------------------------------------ conditions
def _alert(code, key, fp, text, **kw):
    return {"code": code, "key": f"{code}|{key}", "fp": fp, "text": text, **kw}


def _nm(view, s):
    return f"{s}{view['names'].get(s, '')}"


def held_symbols(view: dict) -> list[str]:
    """Shadow holdings; before the shadow is invested, the assets of the latest official target."""
    h = view.get("holdings")
    if h:
        return [r["symbol"] for r in h["rows"]]
    w = view["reference_target"]["weights"]
    return [s for s in view["universe"] if w.get(s, 0.0) > 1e-9]


def check_a1(view, c) -> list[dict]:
    x = _num(c.get("threshold_pct"), 2.0) / 100.0
    held = set(held_symbols(view))
    out = []
    for r in view.get("trend", []):
        d = r.get("dist")
        if r["symbol"] in held and d is not None and abs(d) <= x + EPS:
            side = "above" if d >= 0 else "below"
            out.append(_alert("A1", r["symbol"], side,
                              f"{_nm(view, r['symbol'])} 距40周趋势线 {d * 100:+.1f}%（在均线{'上' if d >= 0 else '下'}方，"
                              f"阈值±{x * 100:g}%）", asset=r["symbol"]))
    return out


def check_a2(view, c) -> list[dict]:
    wi = view["what_if"]
    ch = [x for x in wi["changes"] if x["kind"] != "不变"]
    if not ch:
        return []
    fp = ",".join(sorted(f"{x['symbol']}:{x['kind']}" for x in ch))
    desc = "、".join(f"{x['kind']}{'现金' if x['symbol'] == 'CASH' else _nm(view, x['symbol'])}" for x in ch)
    return [_alert("A2", "whatif", fp, f"若今日为月末，信号将变化：{desc}")]


def check_a3(view, c, a4_active: bool) -> list[dict]:
    n = _num(c.get("sessions"), 2)
    nr = view["next_rebalance"]
    if a4_active or nr["sessions_left"] > n:
        return []
    return [_alert("A3", nr["execution_session"], "near",
                   f"下次调仓临近：{nr['signal_session']} 收盘信号，{nr['execution_session']} 开盘执行，"
                   f"还有 {nr['sessions_left']} 个交易日{'（预计）' if nr['estimated'] else ''}")]


def _order_summary(view) -> str:
    o = view.get("orders")
    if not o or o.get("error"):
        return "（调仓清单不可用）"
    sec = next((s for s in o["sections"] if s["kind"] == "official"), None)
    if sec is None:
        return ""
    parts = []
    for r in sec["rows"]:
        if r["side"] not in ("买入", "卖出"):
            continue
        q = abs(r["delta"])
        qs = f"{q:,.4f}".rstrip("0").rstrip(".") if o["fractional"] else f"{q:,.0f}"
        parts.append(f"{r['side']}{r['symbol']} {qs}份")
    body = "、".join(parts) if parts else "无需操作"
    return f"按{o['capital']:,.0f}{o['currency']}：{body}"


def check_a4(view, c) -> list[dict]:
    rt = view["reference_target"]
    if rt["status"] == "COMPLETE" or rt["pre_freeze"]:
        return []
    tgt = "、".join(f"{_nm(view, s)} {w * 100:.0f}%" for s, w in rt["weights"].items() if w > 1e-9 and s != "CASH")
    cash = rt["weights"].get("CASH", 0.0)
    if cash > 1e-9:
        tgt += ("、" if tgt else "") + f"现金 {cash * 100:.0f}%"
    return [_alert("A4", rt["signal_session"], "pending",
                   f"新月末信号已产生（{rt['signal_session']} 收盘），{rt['execution_session']} 开盘调仓；"
                   f"目标 {tgt}；{_order_summary(view)}")]


def check_a6(view, c) -> list[dict]:
    y = _num(c.get("threshold_pct"), 15.0) / 100.0
    if view["window"]["empty"]:
        return []
    dd = view["strategy"]["drawdown"]
    if dd < -y:
        return [_alert("A6", "drawdown", "breach", f"影子组合距峰值回撤 {dd * 100:.1f}%，超过阈值 {y * 100:g}%")]
    return []


def evaluate(view: dict, mcfg: dict) -> list[dict]:
    """Alerts for one OK market view (A1-A4, A6). Pure function of the view; never mutates it."""
    out = []
    if mcfg["A1"].get("enabled", True):
        out += check_a1(view, mcfg["A1"])
    if mcfg["A2"].get("enabled", True):
        out += check_a2(view, mcfg["A2"])
    a4 = check_a4(view, mcfg["A4"]) if mcfg["A4"].get("enabled", True) else []
    if mcfg["A3"].get("enabled", True):
        out += check_a3(view, mcfg["A3"], bool(a4))
    out += a4
    if mcfg["A6"].get("enabled", True):
        out += check_a6(view, mcfg["A6"])
    return out


def check_a5(market: str, streak: int, c, message: str = "") -> list[dict]:
    n = int(_num(c.get("consecutive_runs"), 2))
    if streak < n:
        return []
    msg = sanitize(message)[:80]
    return [_alert("A5", "fetch", "failed", f"数据获取已连续 {streak} 次失败" + (f"：{msg}" if msg else ""))]


# ------------------------------------------------------------------------------------------------ delivery
_APPLESCRIPT = ["on run argv", "display notification (item 1 of argv) with title (item 2 of argv) "
                "subtitle (item 3 of argv)", "end run"]


def send_notification(title: str, subtitle: str, body: str) -> bool:
    """macOS notification via osascript. Text goes through argv (no string splicing into the script, so quotes and
    backslashes cannot break out). Returns False (and logs) on any failure; never raises."""
    body = (body or "").strip()
    if body.startswith("-"):
        body = " " + body            # a leading '-' could be taken for an option
    cmd = ["osascript"]
    for line in _APPLESCRIPT:
        cmd += ["-e", line]
    cmd += ["--", body, title, subtitle]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
        if r.returncode != 0:
            log.warning("alerts: osascript failed rc=%s: %s", r.returncode, sanitize(r.stderr.strip())[:200])
            return False
        return True
    except Exception as e:  # noqa: BLE001
        log.warning("alerts: osascript error: %s", sanitize(f"{type(e).__name__}: {e}"))
        return False


def compose_body(alerts: list[dict]) -> str:
    parts = [f"【{LABELS[a['code']]}】{a['text']}" for a in alerts]
    body = "；".join(parts) if len(parts) == 1 else "\n".join(f"{i}. {p}" for i, p in enumerate(parts, 1))
    if len(body) > MAX_BODY:
        body = body[:MAX_BODY - 8].rstrip() + "…（详见看板）"
    return sanitize(body)


# ------------------------------------------------------------------------------------------------ state
def load_state(path: Path) -> dict:
    try:
        s = json.loads(Path(path).read_text(encoding="utf-8"))
        return s if isinstance(s, dict) and isinstance(s.get("markets"), dict) else {"markets": {}}
    except Exception:
        return {"markets": {}}


def _save_state(path: Path, state: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def process(views: dict, now_utc: datetime, *, cfg: dict, state_path: Path, notify: bool = True,
            notifier=None) -> dict:
    """Evaluate alerts for every market, notify what is new, persist dedup state.

    Returns {"enabled", "notify", "markets": {key: {"alerts": [...], "last_notified": iso|None, "sent": [codes]}}}.
    State is only written when notifications are really on (so a --no-notify run never uses up an alert).
    Never raises into the caller's rendering path (the caller also guards)."""
    notifier = notifier or send_notification
    live = bool(cfg.get("enabled", True)) and notify
    state = load_state(state_path)
    now_iso = now_utc.astimezone(timezone.utc).isoformat(timespec="seconds")
    result = {"enabled": bool(cfg.get("enabled", True)), "notify": live, "markets": {}}
    for key, view in views.items():
        mcfg = market_cfg(cfg, key)
        prev = state["markets"].get(key, {})
        prev_active = prev.get("active", {})
        once = list(prev.get("once", []))
        streak_prev = int(prev.get("fail_streak", 0))
        if view.get("status") == "ok":
            streak, alerts = 0, evaluate(view, mcfg)
        else:
            streak = streak_prev + 1
            alerts = [{"code": v["code"], "key": k, "fp": v.get("fp"), "text": v["text"]}
                      for k, v in prev_active.items() if v["code"] != "A5"]   # keep what we last knew
            if mcfg["A5"].get("enabled", True):
                alerts += check_a5(key, streak, mcfg["A5"], view.get("message", ""))
        new, sent_ok = [], []
        for a in alerts:
            p = prev_active.get(a["key"])
            if a["code"] in ONCE_CODES:
                fresh = a["key"] not in once
            else:
                fresh = p is None or p.get("fp") != a["fp"]
            if view.get("status") != "ok" and a["code"] != "A5":
                fresh = False                 # carried over from the last good run: already handled then
            a["new"] = fresh
            if fresh:
                new.append(a)
        sent = False
        if live and new:
            sent = bool(notifier(TITLE, view.get("title", key), compose_body(new)))
            log.info("alerts %s: new=%s sent=%s", key, [a["key"] for a in new], sent)
            if sent:
                sent_ok = [a["key"] for a in new]
        else:
            log.info("alerts %s: active=%s new=%s notify=%s", key, [a["key"] for a in alerts],
                     [a["key"] for a in new], live)
        last_notified = prev.get("last_notified")
        if live:
            active = {}
            for a in alerts:
                was_ok = a["key"] in sent_ok
                fp = None if (a["new"] and not was_ok) else a["fp"]      # unsent -> retry next run
                active[a["key"]] = {"code": a["code"], "fp": fp, "text": a["text"],
                                    "notified_at": now_iso if was_ok else prev_active.get(a["key"], {}).get("notified_at")}
                if was_ok and a["code"] in ONCE_CODES and a["key"] not in once:
                    once.append(a["key"])
            if sent:
                last_notified = now_iso
            state["markets"][key] = {"active": active, "once": once[-50:], "fail_streak": streak,
                                     "last_notified": last_notified}
        result["markets"][key] = {"alerts": alerts, "last_notified": last_notified,
                                  "sent": [a["code"] for a in new] if sent else [], "fail_streak": streak}
    if live:
        try:
            _save_state(state_path, state)
        except Exception as e:  # noqa: BLE001
            log.warning("alerts: cannot write state: %s", sanitize(e))
    return result
