from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any
from config.config import UNIVERSE, MIN_REBALANCE_NOTIONAL, SUPPORT_FRACTIONAL_SHARES, EXECUTION_CASH_BUFFER
from src.execution.order_lifecycle import generate_client_order_id

@dataclass
class OrderItem:
    """单个标的的调仓指令明细"""
    symbol: str
    current_weight: float
    research_target_weight: float    # Research V1 原始目标权重 (e.g. 33.333%)
    execution_target_notional: float # 执行层目标金额 (扣除 cash buffer 后的可用预算分配)
    current_value: float
    target_value: float              # 实际下单依据的目标金额 (等于 execution_target_notional)
    trade_value: float
    estimated_qty: float
    side: str                        # 'BUY', 'SELL', 'NO_ACTION'
    current_price: float
    client_order_id: str
    sequence: int = 1                # 逻辑订单序号，针对同一月份标的多次尝试补单时递增
    notional: float = 0.0            # 实际下单依据的金额 (BUY 订单优先使用 notional)
    order_qty: float = 0.0           # 实际下单依据的股数 (SELL 订单优先使用持仓股数以清仓)
    is_dust_ignored: bool = False     # 是否因低于最小交易金额阈值被忽略
    ignored_reason: str = ""

    @property
    def target_weight(self) -> float:
        """兼容属性：返回 Research 目标权重"""
        return self.research_target_weight

@dataclass
class RebalancePlan:
    """完整调仓计划包"""
    rebalance_month: str
    run_id: str
    portfolio_equity: float
    execution_cash_buffer_pct: float = 0.01
    available_execution_capital: float = 0.0
    research_target_portfolio: Dict[str, float] = field(default_factory=dict)
    execution_overlay_portfolio: Dict[str, float] = field(default_factory=dict)
    execution_tracking_error_due_to_buffer: float = 0.01
    sell_orders: List[OrderItem] = field(default_factory=list)
    buy_orders: List[OrderItem] = field(default_factory=list)
    no_action_orders: List[OrderItem] = field(default_factory=list)
    ignored_dust_orders: List[OrderItem] = field(default_factory=list)
    all_orders: List[OrderItem] = field(default_factory=list)
    total_sell_notional: float = 0.0
    total_buy_notional: float = 0.0
    estimated_cash_after: float = 0.0

def generate_rebalance_plan(
    portfolio_equity: float,
    current_positions: Dict[str, Dict[str, float]],
    target_weights: Dict[str, float],
    current_prices: Dict[str, float],
    cash_buffer_pct: float = EXECUTION_CASH_BUFFER,
    min_notional: float = MIN_REBALANCE_NOTIONAL,
    support_fractional: bool = SUPPORT_FRACTIONAL_SHARES,
    rebalance_month: str = "2026-08",
    run_id: str = "init",
    sequence_map: Optional[Dict[str, int]] = None
) -> RebalancePlan:
    """
    根据最新目标权重与当前持仓生成确定性调仓计划 (Rebalance Plan)。
    
    【核心规则与执行缓冲隔离】：
    1. 包含资产池全集以及当前账户已有持仓；
    2. 严格隔离 Research 目标权重与执行层下单预算：
       - Research Target: 保持 33.333% / 50% / 100% 原始目标权重，完全不改动研究回测；
       - Execution Capital: 按 available_capital = equity * (1 - cash_buffer_pct) 预留 1% 现金安全缓冲，
         防止市价单微小滑点、价格漂移或碎股进位导致报单超支被拒；
       - 显式构建 Research Target Portfolio 与 Execution Overlay Portfolio，并记录 tracking error；
    3. 严格显式处理 CASH (若目标权重为空，则全额卖出变现持币)；
    4. 过滤 < min_notional 的微小碎股交易，杜绝不必要的无意义报单；
    5. 执行顺序严格保证：先 SELL (释放资金)，后 BUY (占用资金)；
    6. 为每一个有效指令分配确定性 client_order_id (与 run_id 解耦，杜绝超时重试重复报单)。
    """
    if portfolio_equity <= 0:
        raise ValueError(f"无效的投资组合总资产 (Portfolio Equity): {portfolio_equity}")

    # 计算执行层可用总资金 (扣除现金安全缓冲)
    available_capital = portfolio_equity * (1.0 - cash_buffer_pct)

    # 构建 Research Target Portfolio 与 Execution Overlay Portfolio
    research_portfolio = {}
    overlay_portfolio = {}
    total_research_weight = sum(target_weights.values())
    research_cash_weight = max(0.0, 1.0 - total_research_weight)

    for sym, w in target_weights.items():
        research_portfolio[sym] = w
        overlay_portfolio[sym] = w * (1.0 - cash_buffer_pct)

    research_portfolio["CASH"] = research_cash_weight
    # 执行层 Cash 包括理论 Cash 加上预留的 cash_buffer
    overlay_portfolio["CASH"] = research_cash_weight * (1.0 - cash_buffer_pct) + cash_buffer_pct

    # 合并所有涉及的标的（资产池 + 当前账户已有持仓）
    all_symbols = sorted(list(set(UNIVERSE).union(set(current_positions.keys()))))
    
    plan = RebalancePlan(
        rebalance_month=rebalance_month,
        run_id=run_id,
        portfolio_equity=portfolio_equity,
        execution_cash_buffer_pct=cash_buffer_pct,
        available_execution_capital=available_capital,
        research_target_portfolio=research_portfolio,
        execution_overlay_portfolio=overlay_portfolio,
        execution_tracking_error_due_to_buffer=cash_buffer_pct
    )

    total_current_pos_val = sum(current_positions.get(s, {}).get("market_value", 0.0) for s in all_symbols)
    current_cash = portfolio_equity - total_current_pos_val

    for sym in all_symbols:
        pos_info = current_positions.get(sym, {})
        curr_val = pos_info.get("market_value", 0.0)
        curr_price = current_prices.get(sym, pos_info.get("current_price", 0.0))
        
        curr_w = curr_val / portfolio_equity if portfolio_equity > 0 else 0.0
        r_tgt_w = target_weights.get(sym, 0.0)
        
        # 执行层目标金额由可用资金 * 研究权重决定
        exec_tgt_val = available_capital * r_tgt_w
        
        delta_val = exec_tgt_val - curr_val
        abs_delta = abs(delta_val)

        # 确定方向
        if abs_delta < 1e-4:
            side = "NO_ACTION"
        elif delta_val < 0:
            side = "SELL"
        else:
            side = "BUY"

        # 计算预估股数 (标记为 ESTIMATE)
        if curr_price > 0:
            raw_qty = abs_delta / curr_price
            qty = round(raw_qty, 4) if support_fractional else float(int(raw_qty))
        else:
            qty = 0.0

        # 获取逻辑序号 (支持 residual order 序号递增)
        seq = (sequence_map.get(sym, 1) if sequence_map else 1)

        # 构造确定性的客户端订单 ID (完全移除随机 run_id)
        cid = generate_client_order_id(
            rebalance_month=rebalance_month,
            symbol=sym,
            side=side if side != "NO_ACTION" else "HOLD",
            sequence=seq
        )

        if side == "BUY":
            notional = round(abs_delta, 2)
            order_qty = qty
        elif side == "SELL":
            # 若目标持仓为 0 (清仓)，以已有持仓的精确股数作为卖出股数，彻底避免残留碎股
            if exec_tgt_val < 1e-4 and "qty" in pos_info:
                order_qty = float(pos_info["qty"])
            else:
                order_qty = qty
            notional = round(abs_delta, 2)
        else:
            notional = 0.0
            order_qty = 0.0

        order = OrderItem(
            symbol=sym,
            current_weight=curr_w,
            research_target_weight=r_tgt_w,
            execution_target_notional=exec_tgt_val,
            current_value=curr_val,
            target_value=exec_tgt_val,
            trade_value=delta_val,
            estimated_qty=qty,
            side=side,
            current_price=curr_price,
            client_order_id=cid,
            sequence=seq,
            notional=notional,
            order_qty=order_qty
        )

        # 检查最小交易阈值
        if side != "NO_ACTION" and abs_delta < min_notional:
            order.is_dust_ignored = True
            order.ignored_reason = f"Trade notional ${abs_delta:.2f} < ${min_notional:.2f} threshold"
            plan.ignored_dust_orders.append(order)
            plan.no_action_orders.append(order)
        elif side == "SELL":
            plan.sell_orders.append(order)
            plan.total_sell_notional += abs_delta
        elif side == "BUY":
            plan.buy_orders.append(order)
            plan.total_buy_notional += abs_delta
        else:
            plan.no_action_orders.append(order)

        plan.all_orders.append(order)

    # 预估调仓后的现金 (包含安全缓冲)
    plan.estimated_cash_after = current_cash + plan.total_sell_notional - plan.total_buy_notional
    return plan
