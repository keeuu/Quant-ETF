"""python -m paper_exec status|plan|run [--submit]"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta

from dashboard.safety import load_env, sanitize

from . import executor as ex
from . import journal as jr
from .broker import BrokerError, PaperBroker, SecurityError


def cmd_status(broker, cfg, jpath=None, universe=None, proxy=None) -> list[str]:
    out: list[str] = []
    proxy = proxy or ex.cash_proxy()
    universe = universe or ex.strategy_universe()
    a = broker.get_account()
    ex.say(f"账户状态 {a.get('status')}  净值 ${float(a['equity']):,.2f}  现金 ${float(a['cash']):,.2f}  "
           f"购买力 ${float(a['buying_power']):,.2f}  (paper-api.alpaca.markets)", out)
    c = broker.get_clock()
    ex.say(f"市场时钟 {c['timestamp']}  {'开市' if c.get('is_open') else '休市'}  下次开盘 {c.get('next_open')}", out)
    pos = broker.get_positions()
    strat = set(universe) | {proxy}
    ex.say(f"持仓 {len(pos)} 个：", out)
    for p in sorted(pos, key=lambda p: -float(p["market_value"])):
        tag = "" if p["symbol"] in strat else "  [非策略持仓]"
        ex.say(f"  {p['symbol']:<6} {float(p['qty']):>12.4f} 股  ${float(p['market_value']):>12,.2f}{tag}", out)
    ex.say(f"开关：enabled={cfg['enabled']}（真实提交还需 --submit）", out)
    ev = jr.read(jpath)
    done = [e for e in ev if e.get("event") == "complete"]
    ex.say(f"执行日志：共 {len(ev)} 条记录，已完成信号 {[e['signal_date'] for e in done] or '无'}", out)
    return out


def cmd_plan(broker, cfg, date=None, target_provider=None, universe=None, proxy=None) -> list[str]:
    out: list[str] = []
    proxy = proxy or ex.cash_proxy()
    if date is None:
        date = datetime.fromisoformat(str(broker.get_clock()["timestamp"]).replace("Z", "+00:00")).astimezone(ex.NY).strftime("%Y-%m-%d")
    if target_provider is None:
        from .target import load_target as target_provider
    tgt = target_provider(None)
    universe = universe or tgt.get("universe") or ex.strategy_universe()
    off, wi = tgt["official"], tgt["what_if"]
    ex.say(f"计划日期 {date}；行情数据截至 {tgt['as_of']}", out)
    if off["execution_session"] == date and not off.get("pre_freeze"):
        plan = ex.build_plan(broker, off["weights"], off["signal_session"], cfg, universe, proxy)
        ex.print_plan(plan, out, "【正式信号，演练】")
        st = jr.state(off["signal_session"])
        if st["complete"]:
            ex.say("  该信号已在日志中记录为执行完毕，run 将不会重复下单", out)
    else:
        ex.say(f"官方信号尚未产生：最新官方信号 {off['signal_session']}（执行日 {off['execution_session']}），"
               f"{date} 的月末信号要等 {date} 之前最后一个交易日收盘后才产生", out)
        nr = tgt.get("next_rebalance") or {}
        if nr:
            ex.say(f"下次调仓：信号 {nr.get('signal_session')} → 执行 {nr.get('execution_session')}"
                   f"{'（预计）' if nr.get('estimated') else ''}", out)
    plan = ex.build_plan(broker, wi["weights"], wi["signal_session"], cfg, universe, proxy)
    ex.say("", out)
    ex.say(f"【假设计划，非正式信号，不会被执行】若 {wi['signal_session']} 收盘即为月末信号：", out)
    ex.print_plan(plan, out, "【假设】")
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m paper_exec")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status", help="只读：账户、时钟、持仓、执行日志")
    p = sub.add_parser("plan", help="演练：今天(或 --date)的订单计划")
    p.add_argument("--date", help="执行日 YYYY-MM-DD")
    r = sub.add_parser("run", help="默认演练；enabled=true 且 --submit 且执行日且已开盘才真正下单(模拟账户)")
    r.add_argument("--submit", action="store_true")
    args = ap.parse_args(argv)
    load_env(ex.HERE.parent)
    ex.setup_logging()
    cfg = ex.load_config()
    try:
        if args.cmd == "status":
            cmd_status(PaperBroker(allow_submit=False), cfg)
        elif args.cmd == "plan":
            cmd_plan(PaperBroker(allow_submit=False), cfg, args.date)
        else:
            code, _ = ex.run_cmd(submit=args.submit, cfg=cfg)
            return code
        return 0
    except (SecurityError, BrokerError, RuntimeError) as e:
        print(sanitize(f"错误：{type(e).__name__}: {e}"), file=sys.stderr)
        return 1
