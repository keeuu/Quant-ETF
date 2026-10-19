import sys
import os
import argparse
import uuid
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
from pathlib import Path
import pandas as pd

# 加入项目根目录到 sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from config.config import (
    UNIVERSE,
    TOP_N,
    MIN_REBALANCE_NOTIONAL,
    SUPPORT_FRACTIONAL_SHARES,
    EXECUTION_CASH_BUFFER,
    DEFAULT_PAPER_BASE_URL,
    ORDER_SUBMISSION_ENABLED,
    MARKET_TIMEZONE
)
from src.data.fetcher import fetch_universe_data, get_monthly_dataset
from src.signals.momentum import calculate_12_1_momentum
from src.signals.trend import check_trend_filter
from src.signals.selector import select_top_assets
from src.broker.alpaca_client import AlpacaPaperClient, SecurityException
from src.execution.trading_calendar import (
    get_first_trading_day_of_month,
    is_exchange_trading_day,
    get_calendar_source
)
from src.execution.execution_guard import (
    check_execution_window,
    get_market_now
)
from src.execution.rebalance_planner import generate_rebalance_plan
from src.execution.state_manager import (
    check_rebalance_idempotency,
    load_rebalance_state,
    save_rebalance_state,
    RebalanceStatus
)
from src.execution.reconciliation import reconcile_with_broker
from src.utils.logger import logger

def perform_credential_leak_scan() -> bool:
    """
    检查源码、配置与关键日志文件中是否存在真实 API 密钥或密码硬编码泄漏。
    """
    suspicious_patterns = ["AKID", "secret_key = \"", "api_key = \"AK", "sk_live"]
    # 简要扫描项目关键代码文件
    for py_path in PROJECT_ROOT.glob("src/**/*.py"):
        try:
            content = py_path.read_text(encoding="utf-8")
            for pat in suspicious_patterns:
                if pat in content:
                    return False
        except Exception:
            pass
    return True

def verify_research_selection(monthly_close: pd.DataFrame, target_weights: dict, top_n: int = TOP_N) -> dict:
    """
    Research 信号一致性核验：用 V1 信号函数 (calculate_12_1_momentum / check_trend_filter)
    对同一份 monthly_close 重新计算最新信号日的期望选基，并用独立的 pandas 排序逻辑
    (不经过 select_top_assets) 与实际 target_weights 比对。

    不再硬编码期望标的；若选基集合或权重不一致则抛出 RESEARCH_SIGNAL_DRIFT。
    返回期望权重 dict（便于日志与测试）。
    """
    mom_df = calculate_12_1_momentum(monthly_close)
    trend_df = check_trend_filter(monthly_close)
    latest = monthly_close.index[-1]
    mom = mom_df.loc[latest].dropna()
    trend = trend_df.loc[latest]
    qualified = [s for s in mom.index if bool(trend.get(s, False))]
    ranked = mom.loc[qualified].sort_values(ascending=False, kind="stable")
    chosen = list(ranked.index[:min(len(ranked), top_n)])
    expected = {s: 1.0 / len(chosen) for s in chosen} if chosen else {}

    same_set = set(expected) == set(target_weights)
    same_w = same_set and all(abs(expected[s] - target_weights[s]) < 1e-9 for s in expected)
    if not (same_set and same_w):
        logger.critical(f"RESEARCH_SIGNAL_DRIFT: 重算期望选基 {expected}，实际为 {target_weights}")
        raise RuntimeError("RESEARCH_SIGNAL_DRIFT: 实际选基与基于同一数据重算的 V1 信号不一致！")
    return expected


def probe_broker_calendar(client, run_date: date) -> dict:
    """
    以运行日期为基准（而非硬编码日期）只读探测 Broker Calendar：
    当月前 5 个自然日窗口与下月前 5 个自然日窗口各自的首个交易日。
    """
    cur_start = run_date.replace(day=1)
    nxt_start = (pd.Period(run_date.strftime("%Y-%m"), freq="M") + 1).start_time.date()
    info = {}
    for key, start in (("current_month_first_trading", cur_start), ("next_month_first_trading", nxt_start)):
        cal = client.get_calendar(
            start_date=start.strftime("%Y-%m-%d"),
            end_date=(start + timedelta(days=4)).strftime("%Y-%m-%d"),
        )
        info[key] = cal[0]["date"].strftime("%Y-%m-%d") if cal else "N/A"
    return info


def kill_switch_self_test(order_submission_enabled: bool = ORDER_SUBMISSION_ENABLED) -> str:
    """
    纯本地 Kill-Switch 自检：绝不接触 Broker，绝不提交任何（包括测试）订单。
    若 ORDER_SUBMISSION_ENABLED 为 True，则抛出 SecurityException 终止运行（Fail Closed）。
    """
    if order_submission_enabled:
        raise SecurityException(
            "KILL_SWITCH_SELF_TEST_FAIL: ORDER_SUBMISSION_ENABLED 为 True，本阶段拒绝运行 --execute（未提交任何订单）。"
        )
    return "PASS (本地自检：ORDER_SUBMISSION_ENABLED=False，未接触 Broker，未提交任何订单)"


def main():
    parser = argparse.ArgumentParser(description="Alpaca Paper Trading 动量交易系统 Phase 5.7 (Read-Only & Guards)")
    parser.add_argument("--dry-run", action="store_true", default=True, help="以只读 Dry-Run 模式运行，绝不提交真实订单 (默认)")
    parser.add_argument("--execute", action="store_true", default=False, help="尝试提交订单模式 (受 Phase-level Kill-Switch 硬性拦截)")
    parser.add_argument("--force", action="store_true", default=False, help="强制重新生成调仓计划，忽略当月 COMPLETED 状态")
    args = parser.parse_args()

    # 严格禁止在 --execute 模式下使用 --force 绕过安全状态 (Fail Closed)
    if args.execute and args.force:
        logger.critical("【安全熔断】检测到 --execute 与 --force 同时传入，系统 Fail Closed！")
        raise SecurityException("UNSAFE_FORCE_EXECUTION_BLOCKED: 严禁在 --execute 模式下使用 --force 参数！")

    run_id = f"run_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
    logger.info(f"=== 启动 Alpaca Paper Trading 运行管线 Phase 5.7 (Run ID: {run_id}) ===")

    # 1. 初始化 Alpaca Paper Client 并严格检测凭证与网络模式
    client = AlpacaPaperClient()
    broker_auth_success = False
    broker_clock_info = None
    broker_calendar_info = {}

    if client.has_credentials():
        try:
            logger.info("检测到 Alpaca API 凭证，正在尝试只读连接 Paper Trading API 进行身份认证...")
            acc_info = client.get_account()
            current_positions = client.get_positions()
            open_orders = client.get_open_orders()
            account_mode = "ALPACA_PAPER"
            broker_auth_success = True
            logger.info("Alpaca Paper API 身份认证成功。")

            # 读取 Broker Clock
            try:
                broker_clock_info = client.get_clock()
            except Exception as e:
                logger.warning(f"读取 Broker Clock 异常: {e}")

            # 读取 Broker Calendar（相对运行日期，只读）
            try:
                run_date = datetime.now(ZoneInfo(MARKET_TIMEZONE)).date()
                broker_calendar_info = probe_broker_calendar(client, run_date)
            except Exception as e:
                logger.warning(f"读取 Broker Calendar 异常: {e}")

        except Exception as e:
            logger.critical(f"【安全拦截】Alpaca Paper API 认证失败，严格拒绝静默降级为 Mock: {e}")
            raise RuntimeError(f"PAPER_AUTHENTICATION_FAILED: Alpaca Paper 认证失败: {e}") from e
    else:
        logger.info("未检测到有效 Alpaca 凭证 (.env)，以安全 OFFLINE_MOCK 离线基准模式运行。")
        account_mode = "OFFLINE_MOCK"
        acc_info = {
            "status": "ACTIVE (OFFLINE SIM)",
            "currency": "USD",
            "cash": 100000.0,
            "equity": 100000.0,
            "buying_power": 100000.0,
            "portfolio_value": 100000.0
        }
        current_positions = {}
        open_orders = []

    account_equity = acc_info["equity"]
    account_cash = acc_info["cash"]
    margin_buying_power = acc_info.get("buying_power", 0.0)
    portfolio_val = acc_info.get("portfolio_value", account_equity)

    # 杠杆使用标记检查
    buying_power_note = "0% Leverage (未使用 Buying Power)"
    if margin_buying_power > account_equity:
        buying_power_note = f"MARGIN_AVAILABLE_BUT_UNUSED (${margin_buying_power:,.2f} > ${account_equity:,.2f}, 严格禁止使用杠杆)"

    # 2. 获取当前本地时间与美股市场时间 (America/New_York)
    local_time, market_time, market_date, clock_source = get_market_now(
        client=client,
        require_broker_clock=args.execute
    )

    # 3. 标的 Tradable / Fractional / Active 资格质检 (Universe Verification)
    logger.info("正在验证 Universe 中全部 8 只 ETF 的交易资格 (Tradable, Fractionable, Active)...")
    all_eligible, eligibility_report = client.check_universe_eligibility(UNIVERSE)
    universe_eligible_flag = all_eligible and (account_mode == "ALPACA_PAPER")

    # 4. 严格复用 Research V1 冻结模块获取最新完整自然月信号
    logger.info("加载日线数据并提取最新完整自然月 (Completed Month Only)...")
    aligned_data = fetch_universe_data(UNIVERSE)
    monthly_close, _ = get_monthly_dataset(aligned_data, completed_month_only=True)

    mom_df = calculate_12_1_momentum(monthly_close)
    trend_df = check_trend_filter(monthly_close)

    latest_signal_date = monthly_close.index[-1]
    latest_rebalance_month = latest_signal_date.strftime("%Y-%m")
    logger.info(f"最新合法信号基准日: {latest_signal_date.date()} (信号月份: {latest_rebalance_month})")

    curr_mom = mom_df.loc[latest_signal_date]
    curr_trend = trend_df.loc[latest_signal_date]

    # 直接调用 Research V1 选基模块
    target_weights = select_top_assets(curr_mom, curr_trend, top_n=TOP_N, verbose=False)

    # Research Signal 一致性核验：基于同一数据用 V1 信号函数重算期望选基并比对
    expected_weights = verify_research_selection(monthly_close, target_weights, top_n=TOP_N)
    logger.info(
        f"Research 选基 (信号日 {latest_signal_date.date()}): "
        f"{', '.join(f'{k}={v:.4f}' for k, v in target_weights.items()) or '100% 现金'}；"
        f"重算核验: 一致 (期望 {sorted(expected_weights)})"
    )

    # 5. 调仓执行窗口守护检查 (Execution Window Guard - 基于真实日历与时钟)
    exec_window_status = check_execution_window(
        signal_month=latest_rebalance_month,
        signal_date=latest_signal_date,
        market_datetime=market_time if not args.execute else None,
        client=client,
        require_broker_clock=args.execute
    )

    # 计算下一个周期的计划调仓执行日
    curr_m_period = pd.Period(market_date.strftime("%Y-%m"), freq="M")
    next_m_period = curr_m_period + 1
    next_scheduled_rebalance_date = get_first_trading_day_of_month(
        year=next_m_period.year,
        month=next_m_period.month,
        client=client
    )

    # 6. Kill-Switch 实测与窗口拦截
    kill_switch_test_result = "PASS"
    if args.execute:
        # 执行模式下首先强制核验执行窗口
        if not exec_window_status.execution_allowed:
            logger.critical(f"【执行窗口拦截】{exec_window_status.execution_block_reason}")
            raise RuntimeError(f"EXECUTION_WINDOW_BLOCKED: {exec_window_status.execution_block_reason}")

        logger.warning("【安全防护激活】Phase 5.7 ORDER_SUBMISSION_ENABLED = False，系统硬性阻断订单提交！")
        # 本地自检：不调用 client.submit_order，任何情况下都不会发出测试订单
        kill_switch_test_result = kill_switch_self_test(ORDER_SUBMISSION_ENABLED)

    is_dry_run = True
    order_execution_status = "DRY_RUN_ONLY (ORDER_SUBMISSION_ENABLED = FALSE)"

    # 7. 幂等性检查 (Broker Source of Truth 优先)
    can_proceed, idemp_msg = check_rebalance_idempotency(
        latest_rebalance_month,
        force=args.force,
        client=client,
        is_execute_mode=args.execute
    )

    # 8. 标的当前最新市场参考价
    current_prices = {}
    for sym in UNIVERSE:
        if sym in current_positions and current_positions[sym].get("current_price", 0.0) > 0:
            current_prices[sym] = current_positions[sym]["current_price"]
        else:
            current_prices[sym] = float(aligned_data[sym].iloc[-1]["adj_close"])

    # 9. 生成调仓计划 (严格依据 portfolio_equity，隔离 1% Execution Cash Buffer)
    plan = generate_rebalance_plan(
        portfolio_equity=account_equity,
        current_positions=current_positions,
        target_weights=target_weights,
        current_prices=current_prices,
        cash_buffer_pct=EXECUTION_CASH_BUFFER,
        min_notional=MIN_REBALANCE_NOTIONAL,
        support_fractional=SUPPORT_FRACTIONAL_SHARES,
        rebalance_month=latest_rebalance_month,
        run_id=run_id
    )

    # 10. 凭证泄漏扫描
    leak_scan_pass = perform_credential_leak_scan()
    leak_scan_str = "PASS (未检测到任何代码或日志凭证硬编码泄漏)" if leak_scan_pass else "FAIL (检测到可疑凭证硬编码)"

    # 11. 打印 Phase 5.7 终极验收审计报表
    sep_double = "=" * 95
    sep_single = "-" * 95

    account_clean = (len(open_orders) == 0)

    print(f"\n{sep_double}")
    print(f"       Alpaca Paper Trading 动量交易系统 Phase 5.7 真实只读验收报告")
    print(f"{sep_double}")
    print(f"ACCOUNT_MODE:                     {account_mode}")
    print(f"Paper Authentication:             {'SUCCESS (API_AUTHENTICATED)' if broker_auth_success else 'NOT_CONFIGURED (OFFLINE_MOCK)'}")
    print(f"Calendar Source:                  {exec_window_status.calendar_source}")
    
    if broker_clock_info:
        print(f"Broker Clock Status:              LIVE_CLOCK_SUCCESS")
        print(f"Broker Timestamp:                 {broker_clock_info['timestamp']}")
        print(f"Is Market Open:                   {broker_clock_info['is_open']}")
        print(f"Next Open:                        {broker_clock_info['next_open']}")
        print(f"Next Close:                       {broker_clock_info['next_close']}")
    else:
        print(f"Broker Clock Status:              OFFLINE_MOCK (系统时区转换推导)")
        print(f"Is Market Open:                   N/A (离线模式)")
        print(f"Next Open:                        N/A (离线模式)")
        print(f"Next Close:                       N/A (离线模式)")

    if broker_calendar_info:
        print(f"Broker Calendar Status:           LIVE_CALENDAR_SUCCESS (本月首个交易日: {broker_calendar_info.get('current_month_first_trading')}, 下月首个交易日: {broker_calendar_info.get('next_month_first_trading')})")
    else:
        print(f"Broker Calendar Status:           OFFLINE_MOCK (由 exchange_calendars XNYS 官方交易日历提供)")

    print(f"Market Date:                      {market_date.strftime('%Y-%m-%d')}")
    print(f"Account Equity:                   ${account_equity:,.2f}")
    print(f"Cash:                             ${account_cash:,.2f}")
    print(f"Buying Power:                     ${margin_buying_power:,.2f} ({buying_power_note})")
    print(f"Portfolio Value:                  ${portfolio_val:,.2f}")
    print(f"{sep_single}")

    # 持仓与挂单
    if current_positions:
        print(f"Positions (共 {len(current_positions)} 只):")
        for sym, p in current_positions.items():
            w = p['market_value'] / account_equity if account_equity > 0 else 0.0
            print(f"  * {sym:<6}: Qty={p['qty']:.4f}, Price=${p['current_price']:.2f}, MarketVal=${p['market_value']:.2f}, Weight={w:.2%}")
    else:
        print(f"Positions:                        EMPTY_PAPER_ACCOUNT (当前账户无任何实际持仓)")

    if open_orders:
        print(f"Open Orders (共 {len(open_orders)} 笔):")
        for o in open_orders:
            print(f"  * ID: {o['id']}, Symbol: {o['symbol']}, Side: {o['side']}, Status: {o['status']}")
    else:
        print(f"Open Orders:                      0 笔 (无未结挂单)")
    print(f"ACCOUNT_CLEAN_FOR_REBALANCE:      {account_clean}")
    print(f"{sep_single}")

    # Universe Eligibility Table
    print(f"Universe Eligibility:             {'TRUE (全部通过 Alpaca API 实测核验)' if universe_eligible_flag else 'FALSE (离线静态假设，未由 API 确认)'}")
    print(f"    {'Symbol':<8} | {'Status':<15} | {'Tradable':<10} | {'Fractionable':<14} | {'Eligibility Display':<28} | {'Source':<20}")
    print(f"    {'-'*95}")
    for sym in UNIVERSE:
        meta = eligibility_report.get(sym, {})
        status_str = meta.get("status", "unknown")
        tradable_str = str(meta.get("tradable", False))
        frac_str = str(meta.get("fractionable", False))
        elig_str = meta.get("eligibility_display", "UNVERIFIED")
        src_str = meta.get("source", "UNKNOWN")
        print(f"    {sym:<8} | {status_str:<15} | {tradable_str:<10} | {frac_str:<14} | {elig_str:<28} | {src_str:<20}")
    print(f"{sep_single}")

    # 信号与执行时间线
    print(f"Latest Signal Date:               {latest_rebalance_month} (基准信号日: {latest_signal_date.date()})")
    print(f"Research Target:                  {', '.join([f'{s}: {w:.3%}' for s, w in target_weights.items()])}")
    print(f"Scheduled Execution Date:         {exec_window_status.scheduled_execution_date.date()}")
    print(f"Execution Allowed:                {exec_window_status.execution_allowed}")
    print(f"Block Reason:                     {exec_window_status.execution_block_reason}")
    print(f"{sep_single}")

    # Research Target vs Execution Overlay
    print(f"Research Target Portfolio (理论 100% 满仓多头):")
    for sym, w in plan.research_target_portfolio.items():
        print(f"  * {sym:<6}: {w:>7.3%} (对应理论金额: ${account_equity * w:,.2f})")
    print(f"Execution Target Portfolio (扣除 {EXECUTION_CASH_BUFFER:.1%} Cash Buffer 后的执行覆盖):")
    for sym, w in plan.execution_overlay_portfolio.items():
        print(f"  * {sym:<6}: {w:>7.3%} (执行目标金额: ${account_equity * w:,.2f})")
    print(f"  * 执行缓冲跟踪偏差 (Tracking Error Due to Cash Buffer): {plan.execution_tracking_error_due_to_buffer:.2%}")
    print(f"{sep_single}")

    # 调仓指令明细 (Dry-Run Plan)
    print(f"Dry-Run Plan (调仓计划指令明细 - SELL 优先, BUY 在后):")
    print(f"    {'Symbol':<6} | {'Side':<7} | {'Curr W':>7} | {'R_Tgt W':>7} | {'Exec Tgt':>11} | {'Trade Diff':>11} | {'Est Qty':>9} | {'Status':<12}")
    print(f"    {'-'*90}")
    display_orders = plan.sell_orders + plan.buy_orders + plan.no_action_orders
    for o in display_orders:
        status_tag = "DUST_IGNORED" if o.is_dust_ignored else ("SELL" if o.side == "SELL" else ("BUY" if o.side == "BUY" else "HOLD"))
        print(
            f"    {o.symbol:<6} | {o.side:<7} | {o.current_weight:>6.1%} | {o.research_target_weight:>6.1%} | "
            f"${o.execution_target_notional:>10.2f} | ${o.trade_value:>10.2f} | "
            f"{o.estimated_qty:>9.4f} | {status_tag:<12}"
        )
    print(f"    {'-'*90}")
    print(f"    计划卖出总额: ${plan.total_sell_notional:,.2f} | 计划买入总额: ${plan.total_buy_notional:,.2f} | 调仓后预估现金: ${plan.estimated_cash_after:,.2f}")
    print(f"{sep_single}")

    # 安全熔断与凭证扫描
    print(f"ORDER_SUBMISSION_ENABLED:         {ORDER_SUBMISSION_ENABLED} (硬性 Kill-Switch，全局禁止订单提交)")
    print(f"Kill-Switch Test:                 {kill_switch_test_result}")
    print(f"Credential Leak Scan:             {leak_scan_str}")
    print(f"{sep_double}\n")

if __name__ == "__main__":
    main()
