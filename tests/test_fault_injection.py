import pytest
import time
from pathlib import Path
from unittest.mock import MagicMock
from datetime import datetime, timezone
import pandas as pd

from src.broker.alpaca_client import (
    AlpacaPaperClient,
    SecurityException,
    SubmissionUnknownException
)
from src.execution.order_lifecycle import (
    generate_client_order_id,
    parse_client_order_id,
    is_strategy_order,
    is_terminal,
    is_non_terminal,
    is_filled
)
from src.execution.rebalance_planner import generate_rebalance_plan
from src.execution.state_manager import (
    ExecutionLock,
    ConcurrencyLockError,
    StateCorruptedException,
    RebalanceStatus,
    load_rebalance_state,
    save_rebalance_state,
    check_rebalance_idempotency
)
from src.execution.reconciliation import reconcile_with_broker
from src.execution.executor import execute_rebalance_plan


# ==============================================================================
# 1. test_timeout_after_broker_accept_does_not_resubmit
# ==============================================================================
def test_timeout_after_broker_accept_does_not_resubmit(monkeypatch):
    """
    故障注入场景 1:
    POST submit_order 在 Broker 端成功入库，但 response 在网络返回前发生超时 (TimeoutError)。
    系统严禁盲目重试 POST，必须进入 SUBMISSION_UNKNOWN；
    随后恢复流程使用相同的 client_order_id 查询 Broker，成功找回已创建订单，总提交次数严格等于 1。
    """
    client = AlpacaPaperClient(
        api_key="PKTEST12345678",
        secret_key="SECRET_12345678",
        base_url="https://paper-api.alpaca.markets",
        paper=True
    )
    monkeypatch.setattr("src.broker.alpaca_client.ORDER_SUBMISSION_ENABLED", True)

    submit_count = 0
    cid = "momv1_202610_SPY_BUY_01"
    broker_store = {}

    class MockOrder:
        def __init__(self, client_order_id, symbol="SPY", side="buy", notional=1000.0):
            self.id = "order-uuid-12345"
            self.client_order_id = client_order_id
            self.symbol = symbol
            self.side = MagicMock(value=side)
            self.status = MagicMock(value="new")
            self.type = MagicMock(value="market")
            self.time_in_force = MagicMock(value="day")
            self.notional = notional
            self.qty = None
            self.filled_qty = 0.0
            self.filled_avg_price = 0.0
            self.created_at = datetime.now(timezone.utc)
            self.submitted_at = self.created_at
            self.filled_at = None
            self.canceled_at = None
            self.failed_at = None

    def mock_submit(req):
        nonlocal submit_count
        submit_count += 1
        # 券商端已落单
        broker_store[req.client_order_id] = MockOrder(req.client_order_id, req.symbol, "buy", req.notional)
        # 但在返回响应前，网络发生超时
        raise TimeoutError("Read timed out from broker endpoint")

    monkeypatch.setattr(client._client, "submit_order", mock_submit)
    monkeypatch.setattr(client._client, "get_order_by_client_id", lambda c_id: broker_store.get(c_id))

    # 1. 提交时遭遇超时，必须抛出 SubmissionUnknownException
    with pytest.raises(SubmissionUnknownException) as exc_info:
        client.submit_order(symbol="SPY", side="buy", client_order_id=cid, notional=1000.0)

    assert exc_info.value.client_order_id == cid
    assert submit_count == 1

    # 2. 故障恢复与状态对账：使用原 client_order_id 查单
    recovered = client.get_order_by_client_order_id(cid)
    assert recovered is not None
    assert recovered["client_order_id"] == cid
    assert recovered["id"] == "order-uuid-12345"
    assert recovered["status"] == "new"
    # 验证提交计数依然为 1，绝未重复提交
    assert submit_count == 1


# ==============================================================================
# 2. test_stable_client_order_id_across_restart
# ==============================================================================
def test_stable_client_order_id_across_restart():
    """
    故障注入场景 2:
    模拟进程多次启动/重启，相同 logical order 必须保持完全一致的 client_order_id，
    彻底移除原有的随机 run_id 依赖。
    """
    equity = 100000.0
    target_weights = {"EEM": 1.0 / 3.0, "IWM": 1.0 / 3.0, "QQQ": 1.0 / 3.0}
    current_pos = {}
    prices = {"EEM": 45.0, "IWM": 220.0, "QQQ": 490.0}

    # 模拟启动 1 (run_id 带有随机 uuid 1)
    plan1 = generate_rebalance_plan(
        portfolio_equity=equity,
        current_positions=current_pos,
        target_weights=target_weights,
        current_prices=prices,
        rebalance_month="2026-10",
        run_id="run_20261001_093000_abc111"
    )

    # 模拟重启 2 (run_id 带有全新随机 uuid 2)
    plan2 = generate_rebalance_plan(
        portfolio_equity=equity,
        current_positions=current_pos,
        target_weights=target_weights,
        current_prices=prices,
        rebalance_month="2026-10",
        run_id="run_20261001_093500_xyz999"
    )

    orders1 = {o.symbol: o.client_order_id for o in plan1.buy_orders}
    orders2 = {o.symbol: o.client_order_id for o in plan2.buy_orders}

    assert orders1["EEM"] == orders2["EEM"] == "momv1_202610_EEM_BUY_01"
    assert orders1["IWM"] == orders2["IWM"] == "momv1_202610_IWM_BUY_01"
    assert orders1["QQQ"] == orders2["QQQ"] == "momv1_202610_QQQ_BUY_01"


# ==============================================================================
# 3. test_new_residual_order_gets_new_sequence
# ==============================================================================
def test_new_residual_order_gets_new_sequence():
    """
    故障注入场景 3:
    当原订单已终结 (如 canceled / rejected) 需要补单 (residual order) 时，
    必须递增 logical_order_sequence，绝不能错误复用已被废弃的旧订单 ID。
    """
    equity = 100000.0
    target_weights = {"QQQ": 0.5, "SPY": 0.5}
    current_pos = {}
    prices = {"QQQ": 400.0, "SPY": 500.0}

    # 初次计划 (sequence=1)
    plan1 = generate_rebalance_plan(
        portfolio_equity=equity,
        current_positions=current_pos,
        target_weights=target_weights,
        current_prices=prices,
        rebalance_month="2026-10",
        sequence_map={"QQQ": 1, "SPY": 1}
    )

    # 补单计划 (QQQ 递增为 2)
    plan2 = generate_rebalance_plan(
        portfolio_equity=equity,
        current_positions=current_pos,
        target_weights=target_weights,
        current_prices=prices,
        rebalance_month="2026-10",
        sequence_map={"QQQ": 2, "SPY": 1}
    )

    qqq_order1 = next(o for o in plan1.buy_orders if o.symbol == "QQQ")
    qqq_order2 = next(o for o in plan2.buy_orders if o.symbol == "QQQ")

    assert qqq_order1.client_order_id == "momv1_202610_QQQ_BUY_01"
    assert qqq_order2.client_order_id == "momv1_202610_QQQ_BUY_02"
    assert qqq_order1.client_order_id != qqq_order2.client_order_id

    parsed = parse_client_order_id(qqq_order2.client_order_id)
    assert parsed["sequence"] == 2
    assert parsed["rebalance_month"] == "2026-10"
    assert is_strategy_order(qqq_order2.client_order_id, rebalance_month="2026-10")


# ==============================================================================
# 4. test_partial_fill_restart
# ==============================================================================
def test_partial_fill_restart():
    """
    故障注入场景 4:
    目标买入 $33,000，实际仅部分成交 $12,000 后程序崩溃重启。
    重启后读取实际持仓，仅能计算残余目标 ($21,000 左右)，绝对禁止重新提交完整目标 $33,000。
    """
    total_equity = 100000.0
    target_weights = {"QQQ": 0.33}
    price_qqq = 400.0

    # 崩溃重启后，Broker 实际持仓显示已有 $12,000 (30 股)
    current_pos_after_crash = {
        "QQQ": {
            "qty": 30.0,
            "current_price": 400.0,
            "market_value": 12000.0
        }
    }

    plan_after_restart = generate_rebalance_plan(
        portfolio_equity=total_equity,
        current_positions=current_pos_after_crash,
        target_weights=target_weights,
        current_prices={"QQQ": 400.0},
        cash_buffer_pct=0.01,
        rebalance_month="2026-10"
    )

    qqq_order = next(o for o in plan_after_restart.buy_orders if o.symbol == "QQQ")
    # 目标金额 = 100000 * 0.99 * 0.33 = $32,670
    # 残余买入金额 = 32,670 - 12,000 = $20,670
    assert qqq_order.target_value == pytest.approx(32670.0)
    assert qqq_order.current_value == pytest.approx(12000.0)
    assert qqq_order.notional == pytest.approx(20670.0)
    assert qqq_order.notional < 32670.0
    # 严禁重复全额买入 $32,670
    assert qqq_order.notional != pytest.approx(32670.0)


# ==============================================================================
# 5. test_concurrent_process_lock
# ==============================================================================
def test_concurrent_process_lock(tmp_path):
    """
    故障注入场景 5:
    模拟两个终端或进程 (Terminal A, Terminal B) 并发启动调仓。
    ExecutionLock 必须覆盖 Critical Section，第二个进程必须立刻 Fail Closed 退出。
    """
    lock_file = tmp_path / "execution.lock"

    # 进程 A 成功获取锁
    lock_a = ExecutionLock(lock_file=lock_file)
    lock_a.acquire()

    # 进程 B 尝试获取同一个锁，必须立刻抛出 ConcurrencyLockError
    lock_b = ExecutionLock(lock_file=lock_file)
    with pytest.raises(ConcurrencyLockError, match="已有另一个调仓进程在运行"):
        lock_b.acquire()

    # 进程 A 释放锁
    lock_a.release()

    # 进程 B 现在可以成功获取
    lock_b.acquire()
    lock_b.release()


# ==============================================================================
# 6. test_corrupt_state_file_fail_closed
# ==============================================================================
def test_corrupt_state_file_fail_closed(tmp_path):
    """
    故障注入场景 6:
    state JSON 文件因磁盘损坏或掉电产生非法半截 JSON。
    系统严禁将其误判为'无历史调仓'，必须 Fail Closed 并阻断调仓。
    """
    corrupt_file = tmp_path / "rebalance_state.json"
    corrupt_file.write_text("{\"rebalance_month\": \"2026-10\", \"status\": \"SUBMI...", encoding="utf-8")

    # load_rebalance_state 必须抛出 StateCorruptedException
    with pytest.raises(StateCorruptedException, match="STATE_CORRUPTED"):
        load_rebalance_state(file_path=corrupt_file)

    # 幂等性检查必须返回 False 阻止下单
    can_proceed, msg = check_rebalance_idempotency("2026-10", file_path=corrupt_file)
    assert can_proceed is False
    assert "STATE_CORRUPTED" in msg


# ==============================================================================
# 7. test_deleted_state_recovers_from_broker
# ==============================================================================
def test_deleted_state_recovers_from_broker(tmp_path):
    """
    故障注入场景 7:
    本地 state JSON 被意外误删，但 Broker 端已存在当期的策略订单。
    系统必须依据 Broker 权威订单识别已有执行活动，阻断全额重新买入。
    """
    missing_file = tmp_path / "non_existent_state.json"
    assert not missing_file.exists()

    client = AlpacaPaperClient(
        api_key="PKTEST12345678",
        secret_key="SECRET_12345678",
        base_url="https://paper-api.alpaca.markets",
        paper=True
    )

    # 模拟 Broker 端已有当前月份的订单
    fake_order = {
        "id": "order-1",
        "client_order_id": "momv1_202610_SPY_BUY_01",
        "symbol": "SPY",
        "status": "filled",
        "qty": 10.0,
        "notional": 5000.0,
        "filled_qty": 10.0
    }
    client.get_recent_orders = MagicMock(return_value=[fake_order])

    can_proceed, msg = check_rebalance_idempotency("2026-10", file_path=missing_file, client=client)
    assert can_proceed is False
    assert "券商权威拦截" in msg
    assert "严禁重新下单" in msg


# ==============================================================================
# 8. test_sell_partial_fill_blocks_buy_phase
# ==============================================================================
def test_sell_partial_fill_blocks_buy_phase(tmp_path, monkeypatch):
    """
    故障注入场景 8:
    调仓计划同时包含 SELL 和 BUY。
    SELL 订单未能达到 FILLED 终态 (仅部分成交 partially_filled)。
    系统必须触发 SELL 熔断，绝对禁止启动 BUY 阶段！
    """
    monkeypatch.setattr("src.execution.executor.ORDER_SUBMISSION_ENABLED", True)
    monkeypatch.setattr("src.broker.alpaca_client.ORDER_SUBMISSION_ENABLED", True)

    client = AlpacaPaperClient(
        api_key="PKTEST12345678",
        secret_key="SECRET_12345678",
        base_url="https://paper-api.alpaca.markets",
        paper=True
    )

    # 构造计划：卖出 GLD，买入 QQQ
    current_pos = {
        "GLD": {"qty": 20.0, "current_price": 200.0, "market_value": 4000.0}
    }
    target_weights = {"QQQ": 1.0}
    prices = {"GLD": 200.0, "QQQ": 400.0}

    plan = generate_rebalance_plan(
        portfolio_equity=10000.0,
        current_positions=current_pos,
        target_weights=target_weights,
        current_prices=prices,
        rebalance_month="2026-10"
    )
    assert len(plan.sell_orders) > 0
    assert len(plan.buy_orders) > 0

    submitted_sides = []

    def mock_submit(symbol=None, side=None, client_order_id=None, qty=None, notional=None):
        submitted_sides.append(side.lower())
        return {
            "id": "mock-id-1",
            "client_order_id": client_order_id,
            "status": "new",
            "qty": qty or 0.0,
            "filled_qty": 0.0
        }

    # 券商查询返回：SELL 单仅部分成交 partially_filled
    def mock_get_order(cid):
        return {
            "id": "mock-id-1",
            "client_order_id": cid,
            "status": "partially_filled",
            "qty": 20.0,
            "filled_qty": 5.0,
            "filled_avg_price": 200.0
        }

    client.submit_order = mock_submit
    client.get_order_by_client_order_id = mock_get_order
    client.get_open_orders = MagicMock(return_value=[])
    client.get_recent_orders = MagicMock(return_value=[])
    client.get_positions = MagicMock(return_value=current_pos)
    client.get_account = MagicMock(return_value={"cash": 1000.0, "equity": 10000.0})

    state_file = tmp_path / "rebalance_state.json"
    lock_file = tmp_path / "execution.lock"

    res = execute_rebalance_plan(
        client=client,
        plan=plan,
        state_file=state_file,
        lock_file=lock_file,
        poll_interval=0.01,
        max_wait_seconds=0.05
    )

    assert res["success"] is False
    assert res["buy_phase_blocked"] is True
    assert "未能完全成交" in res["block_reason"]
    # 核心安全断言：BUY 单绝对没有被提交过！
    assert "buy" not in submitted_sides
    assert "sell" in submitted_sides


# ==============================================================================
# 9. test_rejected_order_not_completed
# ==============================================================================
def test_rejected_order_not_completed(tmp_path):
    """
    故障注入场景 9:
    订单被券商拒单 (rejected)。
    对账模块严禁将状态标记为 COMPLETED，必须标记为 FAILED。
    """
    client = AlpacaPaperClient(
        api_key="PKTEST12345678",
        secret_key="SECRET_12345678",
        base_url="https://paper-api.alpaca.markets",
        paper=True
    )

    client.get_open_orders = MagicMock(return_value=[])
    client.get_recent_orders = MagicMock(return_value=[
        {
            "id": "order-rej-1",
            "client_order_id": "momv1_202610_SPY_BUY_01",
            "symbol": "SPY",
            "side": "buy",
            "status": "rejected",
            "qty": 10.0,
            "filled_qty": 0.0
        }
    ])
    client.get_positions = MagicMock(return_value={})
    client.get_account = MagicMock(return_value={"cash": 100000.0, "equity": 100000.0})

    state_file = tmp_path / "rebalance_state.json"
    state = {
        "rebalance_month": "2026-10",
        "status": RebalanceStatus.SUBMITTED,
        "target_portfolio": {"SPY": 1.0},
        "submitted_orders": [
            {"client_order_id": "momv1_202610_SPY_BUY_01", "symbol": "SPY"}
        ]
    }
    save_rebalance_state(state, state_file)

    report = reconcile_with_broker(client, state)

    assert report["reconciled"] is False
    assert report["has_failures"] is True
    assert len(report["failed_orders"]) == 1
    # 状态跃迁至 FAILED，绝不能为 COMPLETED
    assert state["status"] == RebalanceStatus.FAILED


# ==============================================================================
# 10. test_post_retry_disabled
# ==============================================================================
def test_post_retry_disabled(monkeypatch):
    """
    故障注入场景 10:
    模拟 submit_order 遭遇超时异常。
    验证写操作严禁挂接自动重试，底层 submit_order 只能被调用恰好 1 次。
    """
    monkeypatch.setattr("src.broker.alpaca_client.ORDER_SUBMISSION_ENABLED", True)

    client = AlpacaPaperClient(
        api_key="PKTEST12345678",
        secret_key="SECRET_12345678",
        base_url="https://paper-api.alpaca.markets",
        paper=True
    )

    call_count = 0

    def mock_submit_flaky(req):
        nonlocal call_count
        call_count += 1
        raise TimeoutError("Network gateway timeout on POST")

    monkeypatch.setattr(client._client, "submit_order", mock_submit_flaky)

    with pytest.raises(SubmissionUnknownException):
        client.submit_order(
            symbol="SPY",
            side="buy",
            client_order_id="momv1_202610_SPY_BUY_01",
            notional=1000.0
        )

    # 核心安全断言：严禁无脑重复 POST，调用次数必须恰好为 1
    assert call_count == 1
