"""
Alpaca 订单生命周期分类、提交状态与确定性订单 ID 管理模块。
"""

import re
from typing import Optional, Dict, Any

# Alpaca 官方订单状态分类
# 处于该分类的状态表示订单仍在交易所/券商系统中流转或等待成交
NON_TERMINAL_STATUSES = {
    "pending_new",
    "new",
    "accepted",
    "partially_filled",
    "pending_cancel",
    "pending_replace",
    "done_for_day",
}

# 终态：订单生命周期已经结束，不会再有新的状态更新或成交
TERMINAL_STATUSES = {
    "filled",
    "canceled",
    "expired",
    "rejected",
    "stopped",
    "suspended",
    "calculated",
}

# 提交结果分类 (用于处理网络超时与不确定性)
class SubmissionOutcome:
    SUCCESS = "SUBMISSION_SUCCESS"      # 券商已成功确认接收并返回有效订单对象
    FAILED = "SUBMISSION_FAILED"        # 明确拒绝 (如参数错误、已被 Kill-Switch 拦截等)
    UNKNOWN = "SUBMISSION_UNKNOWN"      # 发送超时或连接中断，无法确定券商是否已创建订单

DEFAULT_STRATEGY_ID = "momv1"

def normalize_month_str(month_str: str) -> str:
    """标准化月份字符串，如 '2026-10' 或 '202610' 转换为 '202610'"""
    return month_str.replace("-", "").strip()

def generate_client_order_id(
    rebalance_month: str,
    symbol: str,
    side: str,
    sequence: int = 1,
    strategy_id: str = DEFAULT_STRATEGY_ID
) -> str:
    """
    生成确定性且持久的 Logical Order 客户端订单 ID。
    
    格式: {strategy_id}_{month_yyyymm}_{symbol}_{side}_{seq:02d}
    示例: momv1_202610_EEM_BUY_01
    
    【核心特性】：
    1. Deterministic (确定性): 不含随机 run_id 或毫秒时间戳；
    2. 长度严格在 Alpaca 128 字符限制内 (通常仅 ~25-30 字符)；
    3. 支持故障重试、重启后在 Broker 侧精确找回同一个逻辑订单；
    4. sequence 用于区分同一个逻辑槽位在旧订单终结后的后续补单 (residual order)。
    """
    clean_month = normalize_month_str(rebalance_month)
    clean_sym = symbol.upper().strip()
    clean_side = side.upper().strip()
    seq_str = f"{sequence:02d}"
    return f"{strategy_id}_{clean_month}_{clean_sym}_{clean_side}_{seq_str}"

CLIENT_ORDER_ID_PATTERN = re.compile(
    r"^(?P<strategy_id>[a-zA-Z0-9]+)_(?P<month>\d{6})_(?P<symbol>[a-zA-Z0-9]+)_(?P<side>BUY|SELL)_(?P<sequence>\d+)$"
)

def parse_client_order_id(client_order_id: str) -> Optional[Dict[str, Any]]:
    """
    解析 client_order_id 结构，提取各组成部分。
    若非本系统规范格式则返回 None。
    """
    if not client_order_id:
        return None
    match = CLIENT_ORDER_ID_PATTERN.match(client_order_id)
    if not match:
        return None
    d = match.groupdict()
    return {
        "strategy_id": d["strategy_id"],
        "rebalance_month": f"{d['month'][:4]}-{d['month'][4:]}",
        "symbol": d["symbol"],
        "side": d["side"],
        "sequence": int(d["sequence"])
    }

def is_strategy_order(
    client_order_id: str,
    rebalance_month: Optional[str] = None,
    strategy_id: str = DEFAULT_STRATEGY_ID
) -> bool:
    """
    检查 client_order_id 是否属于特定策略及（可选的）特定调仓月份。
    """
    parsed = parse_client_order_id(client_order_id)
    if not parsed:
        return False
    if parsed["strategy_id"] != strategy_id:
        return False
    if rebalance_month is not None:
        target_month_clean = normalize_month_str(rebalance_month)
        if normalize_month_str(parsed["rebalance_month"]) != target_month_clean:
            return False
    return True

def is_terminal(status: str) -> bool:
    """判断订单是否为终态"""
    return status.lower() in TERMINAL_STATUSES

def is_non_terminal(status: str) -> bool:
    """判断订单是否仍处于未决/流转中"""
    return status.lower() in NON_TERMINAL_STATUSES

def is_filled(status: str) -> bool:
    """判断订单是否已完全成交"""
    return status.lower() == "filled"
