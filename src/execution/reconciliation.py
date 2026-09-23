"""
调仓对账与券商权威状态同步模块 (Broker-Authoritative Reconciliation)。
"""

from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple
from src.broker.alpaca_client import AlpacaPaperClient
from src.execution.order_lifecycle import (
    is_terminal,
    is_non_terminal,
    is_filled,
    is_strategy_order,
    parse_client_order_id,
    generate_client_order_id
)
from src.execution.state_manager import RebalanceStatus, save_rebalance_state
from src.utils.logger import logger

def reconcile_with_broker(
    client: AlpacaPaperClient,
    state: Dict[str, Any],
    state_file: Optional[Path] = None
) -> Dict[str, Any]:
    """
    针对调仓状态执行经纪商端权威状态对账 (Order & Position Reconciliation)。
    
    Args:
        client: 已认证的 Alpaca 客户端。
        state: 当前调仓状态字典（内存中），对账过程中会就地修改 status 字段。
        state_file: 状态持久化文件路径。
            - 传入有效 Path 时，每次状态跃迁后立即写入该文件（生产路径传 REBALANCE_STATE_FILE）。
            - 传入 None（默认）时，仅做内存状态更新，不写入任何文件（测试隔离场景）。

    【核心保证与 Broker Source of Truth】：
    1. Broker Orders + Broker Positions 优先级绝对高于本地 State 文件；
    2. 如果本地 State 文件丢失或存在遗漏，自动根据 stable namespace 扫描并汇聚券商端已有订单；
    3. 细分终态与非终态：
       - NON_TERMINAL: new, pending_new, accepted, partially_filled, pending_cancel 等；
       - TERMINAL: filled, canceled, expired, rejected；
    4. 严禁默认 SUBMITTED == FILLED；
    5. 若存在 REJECTED / CANCELED / EXPIRED，严禁标记 COMPLETED，必须标记 FAILED 或要求补单；
    6. 若存在 PARTIALLY_FILLED，准确提取 filled_qty 与已成交 notional，阻止全额重复下单；
    7. 若存在 SUBMISSION_UNKNOWN 或在券商找不到订单，通过 get_order_by_client_order_id 逐一确认。
    """
    if not client.is_authenticated():
        logger.warning("未配置 Alpaca 客户端，无法执行在线对账。")
        return {"reconciled": False, "reason": "UNAUTHENTICATED"}

    rebalance_month = state.get("rebalance_month", "")
    logger.info(f">>> 开始执行 Alpaca 模拟账户订单状态对账 (周期: {rebalance_month}) <<<")

    # 1. 从券商端拉取权威数据
    open_orders = client.get_open_orders()
    recent_orders = client.get_recent_orders(limit=100)
    current_positions = client.get_positions()
    acc_info = client.get_account()

    # 2. 构建券商端订单索引 (client_order_id -> broker_order)
    broker_orders_by_cid = {}
    for o in recent_orders:
        cid = o.get("client_order_id")
        if cid:
            broker_orders_by_cid[cid] = o
    # 未结挂单具有更新的实时状态，覆盖同 ID 历史记录
    for o in open_orders:
        cid = o.get("client_order_id")
        if cid:
            broker_orders_by_cid[cid] = o

    # 3. 扫描券商端属于当前周期的策略订单，与本地记录合并 (防本地状态丢失/损坏)
    submitted_orders = state.get("submitted_orders", [])
    known_cids = {o.get("client_order_id") for o in submitted_orders if o.get("client_order_id")}

    for cid, b_order in broker_orders_by_cid.items():
        if is_strategy_order(cid, rebalance_month=rebalance_month) and cid not in known_cids:
            logger.info(f"在券商端发现本地未记录的当期订单 [{cid}]，自动纳入对账列表。")
            submitted_orders.append({
                "client_order_id": cid,
                "symbol": b_order.get("symbol"),
                "side": b_order.get("side"),
                "status": b_order.get("status"),
                "qty": b_order.get("qty", 0.0),
                "notional": b_order.get("notional", 0.0),
                "filled_qty": b_order.get("filled_qty", 0.0)
            })
            known_cids.add(cid)

    state["submitted_orders"] = submitted_orders

    reconciled_details = []
    all_terminal = True
    has_failures = False
    partially_filled_orders = []
    filled_orders = []
    failed_orders = []
    unconfirmed_orders = []

    # 4. 逐一比对并同步订单生命周期
    for sub in submitted_orders:
        cid = sub.get("client_order_id")
        actual = broker_orders_by_cid.get(cid)

        # 若缓存列表中未找到，直接向 Broker 发起精确查单 (处理未知提交结果)
        if not actual and cid:
            actual = client.get_order_by_client_order_id(cid)

        if not actual:
            sub["status"] = "UNCONFIRMED_NOT_FOUND"
            all_terminal = False
            unconfirmed_orders.append(cid)
            reconciled_details.append(f"[{cid}] 在券商端未能查到对应委托记录 (UNCONFIRMED)")
            continue

        raw_status = actual.get("status", "unknown").lower()
        sub["status"] = raw_status
        sub["broker_order_id"] = actual.get("id")
        sub["filled_qty"] = float(actual.get("filled_qty", 0.0))
        sub["filled_avg_price"] = float(actual.get("filled_avg_price", 0.0))

        if is_non_terminal(raw_status):
            all_terminal = False
            if raw_status == "partially_filled":
                partially_filled_orders.append(sub)
                reconciled_details.append(f"[{cid}] 部分成交 PARTIALLY_FILLED: 已成交 {sub['filled_qty']} 股")
            else:
                reconciled_details.append(f"[{cid}] 处于未决非终态: status={raw_status}")
        elif is_terminal(raw_status):
            if is_filled(raw_status):
                filled_orders.append(sub)
                reconciled_details.append(f"[{cid}] 已完全成交 FILLED: 成交 {sub['filled_qty']} 股")
            else:
                # 明确失败状态 (rejected, canceled, expired 等)
                has_failures = True
                failed_orders.append(sub)
                reconciled_details.append(f"[{cid}] 处于终止失败状态: status={raw_status} (严禁误判为成功)")

    # 5. 检查持仓是否已对齐目标组合
    target_portfolio = state.get("target_portfolio", {})
    all_target_matched = True
    for sym, tgt_w in target_portfolio.items():
        if tgt_w > 0.01: # 忽略微小舍入
            if sym not in current_positions:
                all_target_matched = False
                reconciled_details.append(f"标的 [{sym}] 目标权重 {tgt_w:.1%} 但在券商持仓中不存在！")

    # 6. 综合判断对账是否圆满达成
    # 必须：全部进入终态 + 无任何失败拒单 + 无部分成交残余 + 无失联订单 + 持仓匹配
    reconciled = (
        all_terminal and 
        (not has_failures) and 
        (len(partially_filled_orders) == 0) and 
        (len(unconfirmed_orders) == 0) and 
        all_target_matched
    )

    logger.info(f"对账结果统计: all_terminal={all_terminal}, has_failures={has_failures}, filled={len(filled_orders)}, partially={len(partially_filled_orders)}, failed={len(failed_orders)}")

    report = {
        "reconciled": reconciled,
        "all_terminal": all_terminal,
        "has_failures": has_failures,
        "all_target_matched": all_target_matched,
        "filled_orders": filled_orders,
        "partially_filled_orders": partially_filled_orders,
        "failed_orders": failed_orders,
        "unconfirmed_orders": unconfirmed_orders,
        "details": reconciled_details,
        "open_order_count": len(open_orders)
    }

    # 7. 更新并持久化状态
    if reconciled:
        state["status"] = RebalanceStatus.COMPLETED
        if state_file is not None:
            save_rebalance_state(state, file_path=state_file)
        logger.info("状态对账完成：所有订单已全部安全结清，状态跃迁至 COMPLETED。")
    elif has_failures:
        state["status"] = RebalanceStatus.FAILED
        if state_file is not None:
            save_rebalance_state(state, file_path=state_file)
        logger.error(f"状态对账告警：存在 {len(failed_orders)} 笔失败/被拒/取消订单，状态跃迁至 FAILED。")
    elif unconfirmed_orders:
        state["status"] = RebalanceStatus.SUBMISSION_UNKNOWN
        if state_file is not None:
            save_rebalance_state(state, file_path=state_file)
        logger.warning(f"状态对账告警：存在 {len(unconfirmed_orders)} 笔未知失联订单，状态保持 SUBMISSION_UNKNOWN。")
    else:
        state["status"] = RebalanceStatus.RECONCILING
        if state_file is not None:
            save_rebalance_state(state, file_path=state_file)
        logger.info("状态对账进行中：订单尚未全部完成，状态保持 RECONCILING。")

    return report

def can_create_residual_sequence(
    client: AlpacaPaperClient,
    rebalance_month: str,
    symbol: str,
    side: str,
    current_sequence: int = 1,
    strategy_id: str = "momv1"
) -> Tuple[bool, int, str]:
    """
    核验特定标的是否允许递增序号进入补单流程 (sequence = current_sequence + 1)。

    【严格准入规则 (Fail-Closed)】：
    只有同时满足以下条件才允许返回 (True, current_sequence + 1, reason)：
    1. 券商端不存在该标的的任何未结挂单 (open_orders 为空，无 active remaining quantity)；
    2. 经券商权威查询确认原订单存在且明确处于终态失败状态 (rejected / canceled / expired)；
    3. 原订单不是 non-terminal (new, accepted, pending_new, partially_filled, pending_cancel, pending_replace)；
    4. 原订单不是 filled (已完全成交无需补单)；
    5. 原订单状态不是 unknown / submission unknown / 404 not found。

    任何其他情况、无法证明安全或查询异常：
    一律返回 (False, current_sequence, reason) 严格 Fail-Closed。
    """
    if client is None:
        return False, current_sequence, "CLIENT_NONE: 未提供券商客户端，严禁生成补单序号！"

    try:
        # 1. 检查券商端是否存在该标的的任何活跃挂单 (active remaining orders)
        open_orders = client.get_open_orders()
        for o in open_orders:
            o_sym = o.get("symbol", "").upper()
            o_cid = o.get("client_order_id", "")
            if o_sym == symbol.upper() or (o_cid and is_strategy_order(o_cid, rebalance_month=rebalance_month) and parse_client_order_id(o_cid).get("symbol") == symbol.upper()):
                return False, current_sequence, (
                    f"ACTIVE_ORDER_EXISTS: 标的 [{symbol}] 在券商端仍存在未结委托挂单 "
                    f"(ID={o.get('id')}, status={o.get('status')})，绝对禁止递增序号！"
                )

        # 2. 查询当前序号对应的逻辑订单状态
        cid = generate_client_order_id(
            rebalance_month=rebalance_month,
            symbol=symbol,
            side=side,
            sequence=current_sequence,
            strategy_id=strategy_id
        )
        prior_order = client.get_order_by_client_order_id(cid)

        # 3. 若券商端未找到该订单，说明发单状态未知或未入库
        if not prior_order:
            return False, current_sequence, (
                f"SUBMISSION_UNKNOWN_OR_NOT_FOUND: 原订单 [{cid}] 在券商端未能查到有效记录，"
                f"状态无法证明为安全终态，绝对禁止递增序号！"
            )

        raw_status = str(prior_order.get("status", "")).lower()

        # 4. 若原订单为非终态 (包括 partially_filled, new, accepted 等)
        if is_non_terminal(raw_status):
            return False, current_sequence, (
                f"NON_TERMINAL_ORDER: 原订单 [{cid}] 处于非终态 [{raw_status}]，"
                f"仍有在途流转或后续成交可能，绝对禁止递增序号！"
            )

        # 5. 若已完全成交 (filled)
        if is_filled(raw_status):
            return False, current_sequence, (
                f"ALREADY_FILLED: 原订单 [{cid}] 已完全成交 (filled)，无需生成补单序号！"
            )

        # 6. 经券商权威确认为明确失败终态 (rejected, canceled, expired)
        if raw_status in ("rejected", "canceled", "expired"):
            next_seq = current_sequence + 1
            return True, next_seq, (
                f"TERMINAL_REJECTED_CONFIRMED: 原订单 [{cid}] 经券商确认为终态失败 [{raw_status}]，"
                f"且无活跃在途挂单，允许进入恢复补单流程生成序号 {next_seq:02d}。"
            )

        # 7. 其他未知状态 Fail Closed
        return False, current_sequence, f"FAIL_CLOSED: 订单状态 [{raw_status}] 无法证明安全，禁止递增序号！"

    except Exception as e:
        logger.critical(f"核验补单序号安全性时发生异常 [{symbol}]: {e}")
        return False, current_sequence, f"EXCEPTION_FAIL_CLOSED: 核验异常: {e}"

def build_residual_sequence_map(
    client: AlpacaPaperClient,
    rebalance_month: str,
    target_items: List[Dict[str, Any]],
    strategy_id: str = "momv1"
) -> Tuple[Dict[str, int], Dict[str, str]]:
    """
    为一批调仓标的构建安全的补单序号映射字典 (sequence_map)。
    target_items: [{"symbol": "SPY", "side": "BUY"}, ...]
    """
    seq_map = {}
    reasons = {}
    for item in target_items:
        sym = item["symbol"]
        side = item["side"]
        can_inc, next_seq, reason = can_create_residual_sequence(
            client=client,
            rebalance_month=rebalance_month,
            symbol=sym,
            side=side,
            current_sequence=1,
            strategy_id=strategy_id
        )
        seq_map[sym] = next_seq if can_inc else 1
        reasons[sym] = reason
    return seq_map, reasons
