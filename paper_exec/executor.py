"""Monthly rebalance executor for the Alpaca PAPER account. Dry run unless config.enabled AND submit flag."""
from __future__ import annotations

import json
import logging
import time
import zoneinfo
from datetime import datetime, timedelta
from pathlib import Path

from dashboard.safety import RedactingFormatter, sanitize

from . import journal as jr
from .broker import BrokerError, PaperBroker, SecurityError, SubmissionUnknown
from .planner import compute_plan

HERE = Path(__file__).resolve().parent
CONFIG_PATH = HERE / "config.json"
LOG_PATH = HERE.parent / "logs" / "paper_exec.log"
NY = zoneinfo.ZoneInfo("America/New_York")
log = logging.getLogger("paper_exec")
TERMINAL = {"filled", "canceled", "expired", "rejected", "done_for_day", "replaced", "suspended"}
PROXY_DEFAULT = "SGOV"

DEFAULT_CFG = {"enabled": False, "min_order_usd": 1.0, "max_minutes_after_open": 90, "poll_seconds": 60,
               "poll_interval_seconds": 3, "cash_buffer": 0.002}


def setup_logging(path: Path = LOG_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    log.setLevel(logging.INFO)
    for h in list(log.handlers):
        log.removeHandler(h)
        h.close()
    for h in (logging.FileHandler(path, encoding="utf-8"), logging.StreamHandler()):
        h.setFormatter(RedactingFormatter("%(asctime)s %(levelname)s %(message)s"))
        log.addHandler(h)
    log.propagate = False


def load_config(path: Path | None = None) -> dict:
    cfg = dict(DEFAULT_CFG)
    try:
        raw = json.loads(Path(path or CONFIG_PATH).read_text(encoding="utf-8"))
        cfg.update({k: v for k, v in raw.items() if not k.startswith("_")})
    except Exception:  # noqa: BLE001  unreadable config -> defaults, i.e. enabled False
        pass
    cfg["enabled"] = cfg.get("enabled") is True            # only a literal JSON true enables
    return cfg


def cash_proxy() -> str:
    try:
        from dashboard.config import load_account
        return str(load_account()["US"]["cash_proxy"])
    except Exception:  # noqa: BLE001
        return PROXY_DEFAULT


def strategy_universe() -> tuple:
    from dashboard.config import us_spec
    return tuple(us_spec().universe)


def say(msg: str, out: list | None = None) -> None:
    msg = sanitize(msg)
    log.info(msg)
    if out is not None:
        out.append(msg)


def positions_dict(raw: list) -> dict:
    out = {}
    for p in raw:
        out[p["symbol"]] = {"qty": p["qty"], "market_value": float(p["market_value"]),
                            "price": float(p["current_price"]) if p.get("current_price") else None,
                            "side": p.get("side", "long")}
    return out


def fractionable_map(broker, symbols) -> dict:
    out = {}
    for s in symbols:
        a = broker.get_asset(s)
        out[s] = bool(a.get("fractionable")) and bool(a.get("tradable")) and a.get("status") == "active"
    return out


def read_account(broker) -> dict:
    a = broker.get_account()
    if str(a.get("status", "")).upper() != "ACTIVE" or a.get("trading_blocked") or a.get("account_blocked"):
        raise BrokerError(0, "模拟账户状态非 ACTIVE 或被限制交易")
    return a


def fmt_orders(orders: list) -> list[str]:
    lines = []
    for o in orders:
        amt = f"${o['notional']:,.2f}" if "notional" in o else f"{o['qty']} 股"
        lines.append(f"  {'卖出' if o['side'] == 'sell' else '买入'} {o['symbol']:<5} {amt:>14}  (约 ${o['est_usd']:,.2f})  {o['client_order_id']}")
    return lines or ["  (无需下单)"]


def build_plan(broker, target_weights_: dict, signal_date: str, cfg: dict, universe, proxy: str) -> dict:
    acct = read_account(broker)
    pos = positions_dict(broker.get_positions())
    frac = fractionable_map(broker, sorted(set(universe) | {proxy}))
    plan = compute_plan(signal_date=signal_date, weights=target_weights_, universe=universe, proxy=proxy,
                        equity=float(acct["equity"]), positions=pos, fractionable=frac,
                        min_usd=float(cfg["min_order_usd"]))
    plan["cash"] = float(acct["cash"])
    return plan


def print_plan(plan: dict, out: list, label: str = "") -> None:
    say(f"{label}信号日 {plan['signal_date']}：账户净值 ${plan['equity']:,.2f}，策略可用 ${plan['strategy_base']:,.2f}", out)
    say("  目标权重：" + "、".join(f"{s} {w:.1%}" for s, w in sorted(plan["target_weights"].items())), out)
    if plan["non_strategy"]:
        say("  非策略持仓(不触碰)：" + "、".join(f"{s} ${v:,.2f}" for s, v in plan["non_strategy"].items()), out)
    for ln in fmt_orders(plan["orders"]):
        say(ln, out)
    for n in plan["notes"]:
        say(f"  注：{n}", out)


def poll_orders(broker, ids: list[str], cfg: dict, sleep=time.sleep, now=time.monotonic) -> dict:
    """Read order states by client_order_id until all are terminal or poll_seconds elapse."""
    deadline = now() + float(cfg["poll_seconds"])
    res: dict = {}
    while True:
        for cid in ids:
            if res.get(cid) and res[cid]["status"] in TERMINAL:
                continue
            res[cid] = broker.get_order_by_client_id(cid)
            if res[cid] is None:
                res[cid] = {"status": "missing", "client_order_id": cid}
        if all(r["status"] in TERMINAL or r["status"] == "missing" for r in res.values()) or now() >= deadline:
            return res
        sleep(float(cfg["poll_interval_seconds"]))


def brief(o: dict) -> dict:
    return {k: o.get(k) for k in ("client_order_id", "symbol", "side", "status", "qty", "notional", "filled_qty",
                                  "filled_avg_price", "filled_at", "id")}


def submit_one(broker, o: dict, scale: float, signal_date: str, jpath) -> str:
    """Send one order unless the broker already knows its client_order_id. Returns sent|exists|unknown_found|failed."""
    cid = o["client_order_id"]
    ex = broker.get_order_by_client_id(cid)
    if ex is not None:
        jr.append({"signal_date": signal_date, "event": "exists", "order": brief(ex)}, jpath)
        return "exists"
    kw = {"symbol": o["symbol"], "side": o["side"], "client_order_id": cid}
    if "notional" in o:
        kw["notional"] = round(o["notional"] * scale, 2)
    else:
        kw["qty"] = o["qty"]
    if kw.get("notional") is not None and kw["notional"] < 1.0:
        jr.append({"signal_date": signal_date, "event": "skipped", "client_order_id": cid, "reason": "notional<1"}, jpath)
        return "skipped"
    try:
        resp = broker.submit_market_order(**kw)
        jr.append({"signal_date": signal_date, "event": "submitted", "order": brief(resp)}, jpath)
        return "sent"
    except SubmissionUnknown as e:
        jr.append({"signal_date": signal_date, "event": "unknown", "client_order_id": cid, "error": str(e)}, jpath)
        found = broker.get_order_by_client_id(cid)
        return "unknown_found" if found else "failed"
    except BrokerError as e:
        jr.append({"signal_date": signal_date, "event": "rejected", "client_order_id": cid, "error": str(e)}, jpath)
        return "failed"


def execute_plan(broker, plan: dict, cfg: dict, jpath, sleep=time.sleep, now=time.monotonic) -> dict:
    sd = plan["signal_date"]
    orders = plan["orders"]
    sells = [o for o in orders if o["side"] == "sell"]
    buys = [o for o in orders if o["side"] == "buy"]
    status = {}
    for o in sells:
        status[o["client_order_id"]] = submit_one(broker, o, 1.0, sd, jpath)
    held_back = False
    if sells:
        st = poll_orders(broker, [o["client_order_id"] for o in sells], cfg, sleep, now)
        if any(r["status"] != "filled" for r in st.values()):
            held_back = bool(buys)
    scale = 1.0
    if buys and not held_back:
        acct = broker.get_account()
        avail = min(float(acct["cash"]), float(acct.get("buying_power", acct["cash"]))) * (1 - float(cfg["cash_buffer"]))
        total = sum(o.get("notional", 0.0) for o in buys if broker.get_order_by_client_id(o["client_order_id"]) is None)
        if total > avail > 0:
            scale = avail / total
            jr.append({"signal_date": sd, "event": "scaled_buys", "scale": scale}, jpath)
        elif avail <= 0 and total > 0:
            held_back = True
    if not held_back:
        for o in buys:
            status[o["client_order_id"]] = submit_one(broker, o, scale, sd, jpath)
    ids = [o["client_order_id"] for o in orders if status.get(o["client_order_id"]) not in (None, "failed", "skipped")]
    final = poll_orders(broker, ids, cfg, sleep, now) if ids else {}
    for cid, r in final.items():
        jr.append({"signal_date": sd, "event": "fill", "order": brief(r)}, jpath)
    all_known = all(status.get(o["client_order_id"]) in ("sent", "exists", "unknown_found", "skipped") for o in orders)
    complete = all_known and not held_back
    if complete:
        jr.append({"signal_date": sd, "event": "complete", "n_orders": len(orders),
                   "statuses": {c: r["status"] for c, r in final.items()}}, jpath)
    return {"status": status, "final": final, "complete": complete, "held_back": held_back}


def notify_text(plan: dict, res: dict) -> str:
    parts = []
    for side, word in (("sell", "卖出"), ("buy", "买入")):
        xs = [f"{o['symbol']} ${o['est_usd']:,.0f}" for o in plan["orders"] if o["side"] == side]
        if xs:
            parts.append(word + " " + "、".join(xs))
    body = "；".join(parts) or "无需交易"
    if not res["complete"]:
        body += "（部分未完成，将在下次运行续做）"
    return f"美股模拟账户已按信号调仓：{body}"


def run_cmd(*, submit: bool = False, cfg: dict | None = None, broker_factory=None, target_provider=None,
            notifier=None, jpath: Path | None = None, universe=None, proxy: str | None = None,
            sleep=time.sleep, now=time.monotonic, utcnow=None) -> tuple[int, list[str]]:
    out: list[str] = []
    cfg = cfg if cfg is not None else load_config()
    real = bool(cfg.get("enabled") is True and submit)
    broker = (broker_factory or PaperBroker)(allow_submit=real)
    say(f"模式：{'真实提交(模拟账户)' if real else '演练(不下单)'}"
        + ("" if real else f"  [enabled={cfg.get('enabled')}, --submit={submit}]"), out)
    try:
        clock = broker.get_clock()
        ts = datetime.fromisoformat(str(clock["timestamp"]).replace("Z", "+00:00")).astimezone(NY)
        today = ts.strftime("%Y-%m-%d")
        cal = broker.get_calendar((ts - timedelta(days=12)).strftime("%Y-%m-%d"), today)
        days = sorted(c["date"] for c in cal)
        if today not in days:
            say(f"今天不是执行日：{today} 美股休市", out)
            return 0, out
        i = days.index(today)
        if i == 0 or days[i - 1][:7] == today[:7]:
            say(f"今天不是执行日：{today} 不是当月第一个交易日", out)
            return 0, out
        signal_guess = days[i - 1]
        if jr.state(signal_guess, jpath)["complete"]:
            say(f"信号 {signal_guess} 已执行完毕，无需重复(幂等)", out)
            return 0, out
        if not clock.get("is_open"):
            say("未开盘：美股尚未开市，不执行", out)
            return 0, out
        row = next(c for c in cal if c["date"] == today)
        hh, mm = str(row["open"]).split(":")[:2]
        opened = datetime.fromisoformat(today).replace(hour=int(hh), minute=int(mm), tzinfo=NY)
        mins = (ts - opened).total_seconds() / 60
        if mins > float(cfg["max_minutes_after_open"]):
            say(f"已超出执行窗口：开盘后 {mins:.0f} 分钟 > {cfg['max_minutes_after_open']} 分钟，不执行", out)
            return 0, out
        tgt = (target_provider or __import__("paper_exec.target", fromlist=["load_target"]).load_target)(
            utcnow() if utcnow else None)
        off = tgt["official"]
        if off["execution_session"] != today or off.get("pre_freeze"):
            say(f"今天不是执行日：最新官方信号 {off['signal_session']} 的执行日为 {off['execution_session']}", out)
            return 0, out
        sd = off["signal_session"]
        universe = universe or tgt.get("universe") or strategy_universe()
        proxy = proxy or cash_proxy()
        st = jr.state(sd, jpath)
        if st["complete"]:
            say(f"信号 {sd} 已执行完毕，无需重复(幂等)", out)
            return 0, out
        if st["plan"] is not None:
            plan = {"signal_date": sd, "orders": st["plan"], "equity": 0.0, "strategy_base": 0.0, "target_weights": {},
                    "non_strategy": {}, "notes": ["续做：沿用日志中已记录的订单计划"]}
            say("检测到该信号已有未完成的执行记录，续做缺失订单(不会重发已存在的订单)", out)
        else:
            plan = build_plan(broker, off["weights"], sd, cfg, universe, proxy)
        print_plan(plan, out, "【正式信号】")
        if not real:
            jr.append({"signal_date": sd, "event": "dry_run", "n_orders": len(plan["orders"])}, jpath)
            say("演练结束：未提交任何订单", out)
            return 0, out
        if st["plan"] is None:
            jr.append({"signal_date": sd, "event": "plan", "orders": plan["orders"], "equity": plan["equity"],
                       "target_weights": plan["target_weights"], "non_strategy": plan["non_strategy"]}, jpath)
        res = execute_plan(broker, plan, cfg, jpath, sleep, now)
        for cid, r in res["final"].items():
            say(f"  {cid}: {r['status']} 成交 {r.get('filled_qty')} @ {r.get('filled_avg_price')}", out)
        text = notify_text(plan, res)
        say(text, out)
        if notifier is None:
            from dashboard.alerts import send_notification as notifier
        notifier("量化看板", "美股模拟账户", text)
        return (0 if res["complete"] else 2), out
    except (SecurityError, BrokerError) as e:
        say(f"错误：{sanitize(e)}", out)
        return 1, out
