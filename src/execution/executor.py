"""
调仓执行引擎 (Execution Engine with Strict Lifecycle & Safety Controls)。
"""

import time
from pathlib import Path
from typing import Dict, List, Any, Optional
from config.config import ORDER_SUBMISSION_ENABLED, REBALANCE_STATE_FILE
from src.broker.alpaca_client import AlpacaPaperClient, SecurityException, SubmissionUnknownException
from src.execution.rebalance_planner import RebalancePlan, OrderItem
from src.execution.order_lifecycle import (
    is_terminal,
    is_filled,
    SubmissionOutcome
)
from src.execution.state_manager import (
    ExecutionLock,
    RebalanceStatus,
    load_rebalance_state,
    save_rebalance_state,
    check_rebalance_idempotency
)
from src.execution.reconciliation import reconcile_with_broker
from src.utils.logger import logger

class ExecutionPhaseException(Exception):
    """调仓阶段执行异常"""
    pass

def execute_rebalance_plan(
    client: AlpacaPaperClient,
    plan: RebalancePlan,
    state_file: Path = REBALANCE_STATE_FILE,
    lock_file: Optional[Path] = None,
    poll_interval: float = 0.5,
    max_wait_seconds: float = 30.0,
    force: bool = False
) -> Dict[str, Any]:
    """
    严谨执行调仓计划 (Execution Overlay)。
    
    【安全执行流水线】：
    1. 获取进程级执行锁 (ExecutionLock)，覆盖整个 Critical Section，防止双重并发；
    2. 检查全局 Kill-Switch (ORDER_SUBMISSION_ENABLED)，若为 False 严格抛出 SecurityException；
    3. 幂等性检查 (Broker Source of Truth 优先)，阻止重复下单；
    4. 阶段一 (SELL Phase)：仅提交卖单。严格轮询等待所有卖单达到 FILLED 终态；
       - 若有卖单处于 PARTIALLY_FILLED, REJECTED, EXPIRED, CANCELED 或 UNKNOWN：
         立即熔断并中断执行，绝对禁止进入 BUY Phase！
    5. 资金与持仓刷新：重新读取券商端真实现金与持仓，确保 Cash >= 目标买入金额 (严格 No Leverage，不使用额外 Buying Power)；
    6. 阶段二 (BUY Phase)：提交买单；
    7. 对账闭环：调用 reconcile_with_broker 确认所有订单结清与持仓对齐。
    """
    # 1. 进程级并发排他锁覆盖整个 Critical Section
    with ExecutionLock(lock_file=lock_file):
        # 严格禁止在真实执行模式下通过 force 绕过任何安全状态
        if force:
            logger.critical("【安全熔断】执行模式下严禁使用 force 绕过安全状态 (UNSAFE_FORCE_EXECUTION_BLOCKED)！")
            raise SecurityException(
                "UNSAFE_FORCE_EXECUTION_BLOCKED: 严禁在执行模式下使用 force 绕过安全状态！"
            )

        # 2. 检查 Kill-Switch
        if not ORDER_SUBMISSION_ENABLED:
            raise SecurityException(
                "【安全熔断拦截 (Kill-Switch)】ORDER_SUBMISSION_ENABLED = False！"
                "当前系统处于只读验证阶段，严禁提交任何 Paper 或 Live 订单。"
            )

        if not client.is_authenticated():
            raise RuntimeError("Alpaca 客户端未认证，无法执行调仓。")

        # 3. 幂等性与未决状态检查
        can_proceed, idemp_msg = check_rebalance_idempotency(
            rebalance_month=plan.rebalance_month,
            force=force,
            file_path=state_file,
            client=client
        )
        if not can_proceed:
            raise RuntimeError(f"幂等性安全拦截: {idemp_msg}")

        logger.info(f"=== 开始进入订单执行阶段: 周期={plan.rebalance_month}, 计划标的={len(plan.all_orders)} ===")

        state = {
            "rebalance_month": plan.rebalance_month,
            "run_id": plan.run_id,
            "status": RebalanceStatus.SUBMITTING,
            "target_portfolio": plan.execution_overlay_portfolio,
            "submitted_orders": []
        }
        save_rebalance_state(state, file_path=state_file)

        # ----------------------------------------------------------------------
        # 4. Phase 1: SELL Phase (卖出优先，释放资金)
        # ----------------------------------------------------------------------
        logger.info(f">>> [Phase 1: SELL] 计划提交 {len(plan.sell_orders)} 笔卖单 <<<")
        sell_order_records = []

        for sell_item in plan.sell_orders:
            cid = sell_item.client_order_id
            sym = sell_item.symbol
            qty = sell_item.order_qty

            logger.info(f"正在提交卖单: {sym}, 股数={qty}, cid={cid}")
            try:
                resp = client.submit_order(
                    symbol=sym,
                    side="sell",
                    client_order_id=cid,
                    qty=qty
                )
                sell_record = {
                    "client_order_id": cid,
                    "symbol": sym,
                    "side": "sell",
                    "status": resp.get("status", "submitted"),
                    "broker_order_id": resp.get("id"),
                    "qty": qty,
                    "filled_qty": resp.get("filled_qty", 0.0)
                }
            except SubmissionUnknownException as e:
                logger.critical(f"卖单提交状态未知 [{cid}]: {e}")
                sell_record = {
                    "client_order_id": cid,
                    "symbol": sym,
                    "side": "sell",
                    "status": RebalanceStatus.SUBMISSION_UNKNOWN,
                    "error": str(e)
                }
                state["status"] = RebalanceStatus.SUBMISSION_UNKNOWN
            except Exception as e:
                logger.critical(f"卖单提交明确失败 [{cid}]: {e}")
                sell_record = {
                    "client_order_id": cid,
                    "symbol": sym,
                    "side": "sell",
                    "status": "rejected",
                    "error": str(e)
                }

            sell_order_records.append(sell_record)
            state["submitted_orders"].append(sell_record)
            save_rebalance_state(state, file_path=state_file)

            # 若发单遭遇未知或失败，直接终止后续发单
            if sell_record["status"] in (RebalanceStatus.SUBMISSION_UNKNOWN, "rejected"):
                break

        # 验证所有 SELL 订单必须全部达到 FILLED 安全状态
        logger.info("正在验证所有 SELL 订单执行结果...")
        all_sells_filled = True
        failed_sell_reason = ""

        for s_rec in sell_order_records:
            cid = s_rec["client_order_id"]
            # 轮询检查券商端状态
            start_wait = time.time()
            order_info = None
            while time.time() - start_wait < max_wait_seconds:
                try:
                    order_info = client.get_order_by_client_order_id(cid)
                    if order_info and is_terminal(order_info.get("status", "")):
                        break
                except Exception as e:
                    logger.warning(f"查询卖单 [{cid}] 状态异常: {e}")
                time.sleep(poll_interval)

            if not order_info:
                all_sells_filled = False
                failed_sell_reason = f"卖单 [{cid}] 未能在券商端查询到有效记录 (TIMEOUT/UNKNOWN)"
                break

            latest_status = order_info.get("status", "").lower()
            s_rec["status"] = latest_status
            s_rec["filled_qty"] = float(order_info.get("filled_qty", 0.0))

            if not is_filled(latest_status):
                all_sells_filled = False
                failed_sell_reason = f"卖单 [{cid}] 未能完全成交，当前状态为 [{latest_status}], 已成交股数: {s_rec['filled_qty']}"
                break

        save_rebalance_state(state, file_path=state_file)

        if not all_sells_filled:
            logger.critical(f"【SELL 熔断激活】{failed_sell_reason}！严格禁止进入 BUY Phase，立即转入对账。")
            reconcile_report = reconcile_with_broker(client, state, state_file=state_file)
            return {
                "success": False,
                "buy_phase_blocked": True,
                "block_reason": failed_sell_reason,
                "reconcile_report": reconcile_report
            }

        logger.info(">>> 所有 SELL 订单已成功完全成交 (FILLED) <<<")

        # ----------------------------------------------------------------------
        # 5. Phase 2: Cash & Position Refresh (严禁杠杆核验)
        # ----------------------------------------------------------------------
        logger.info("正在从券商端刷新账户最新现金与持仓...")
        refreshed_acc = client.get_account()
        refreshed_cash = refreshed_acc["cash"]
        total_buy_needed = plan.total_buy_notional

        logger.info(f"当前账户可用净现金: ${refreshed_cash:,.2f}, 计划买入总额: ${total_buy_needed:,.2f}")
        # 严格遵守 NO LEVERAGE 规则：买入总预算严禁超越实际净现金
        if total_buy_needed > refreshed_cash:
            reason = f"【资金不足熔断】计划买入总额 (${total_buy_needed:,.2f}) 超过了可用净现金 (${refreshed_cash:,.2f})！系统严禁借用 Margin 杠杆，终止买入。"
            logger.critical(reason)
            state["status"] = RebalanceStatus.FAILED
            save_rebalance_state(state, file_path=state_file)
            reconcile_report = reconcile_with_broker(client, state, state_file=state_file)
            return {
                "success": False,
                "buy_phase_blocked": True,
                "block_reason": reason,
                "reconcile_report": reconcile_report
            }

        # ----------------------------------------------------------------------
        # 6. Phase 3: BUY Phase (买入占用资金)
        # ----------------------------------------------------------------------
        logger.info(f">>> [Phase 3: BUY] 计划提交 {len(plan.buy_orders)} 笔买单 <<<")
        for buy_item in plan.buy_orders:
            cid = buy_item.client_order_id
            sym = buy_item.symbol
            notional = buy_item.notional

            logger.info(f"正在提交买单: {sym}, 金额=${notional}, cid={cid}")
            try:
                resp = client.submit_order(
                    symbol=sym,
                    side="buy",
                    client_order_id=cid,
                    notional=notional
                )
                buy_record = {
                    "client_order_id": cid,
                    "symbol": sym,
                    "side": "buy",
                    "status": resp.get("status", "submitted"),
                    "broker_order_id": resp.get("id"),
                    "notional": notional,
                    "filled_qty": resp.get("filled_qty", 0.0)
                }
            except SubmissionUnknownException as e:
                logger.critical(f"买单提交状态未知 [{cid}]: {e}")
                buy_record = {
                    "client_order_id": cid,
                    "symbol": sym,
                    "side": "buy",
                    "status": RebalanceStatus.SUBMISSION_UNKNOWN,
                    "error": str(e)
                }
                state["status"] = RebalanceStatus.SUBMISSION_UNKNOWN
            except Exception as e:
                logger.critical(f"买单提交明确失败 [{cid}]: {e}")
                buy_record = {
                    "client_order_id": cid,
                    "symbol": sym,
                    "side": "buy",
                    "status": "rejected",
                    "error": str(e)
                }

            state["submitted_orders"].append(buy_record)
            save_rebalance_state(state, file_path=state_file)

        # ----------------------------------------------------------------------
        # 7. Phase 4: Final Reconciliation
        # ----------------------------------------------------------------------
        state["status"] = RebalanceStatus.RECONCILING
        save_rebalance_state(state, file_path=state_file)
        reconcile_report = reconcile_with_broker(client, state, state_file=state_file)

        return {
            "success": reconcile_report["reconciled"],
            "buy_phase_blocked": False,
            "reconcile_report": reconcile_report
        }
