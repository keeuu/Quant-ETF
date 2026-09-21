from typing import NamedTuple, Optional, Tuple, Any, Union
from datetime import datetime, date, time, timezone
from zoneinfo import ZoneInfo
import pandas as pd
from config.config import MARKET_TIMEZONE
from src.execution.trading_calendar import (
    get_first_trading_day_of_month,
    is_exchange_trading_day,
    get_calendar_source
)
from src.utils.logger import logger

MARKET_TZ = ZoneInfo(MARKET_TIMEZONE)
EXECUTION_WINDOW_START = time(9, 30, 0)
EXECUTION_WINDOW_END = time(9, 35, 0)

class ExecutionWindowStatus(NamedTuple):
    signal_month: str
    signal_date: pd.Timestamp
    scheduled_execution_date: pd.Timestamp
    market_datetime: datetime
    market_date: date
    execution_allowed: bool
    execution_block_reason: str
    calendar_source: str
    clock_source: str
    is_market_open: bool = False

def get_market_now(
    client: Optional[Any] = None,
    require_broker_clock: bool = False
) -> Tuple[datetime, datetime, date, str]:
    """
    获取当前系统本地时间与美股市场官方时间 (America/New_York)。
    
    规则：
    1. 在线 Alpaca Paper 模式下，优先调用 Broker Clock API 获取权威市场时钟；
    2. 若 require_broker_clock 为 True (真实 execute 路径)，如果无法从 Broker 获取时钟则直接抛出异常 Fail Closed，
       严禁降级使用本地系统时间或其它时区推导；
    3. 离线模式下，获取系统当前时间并准确转换为 America/New_York 时区；
    返回: (local_now, market_time_et, market_date, clock_source)
    """
    local_now = datetime.now().astimezone()
    clock_source = "SYSTEM_NEW_YORK_CLOCK"

    if client is not None and (getattr(client, "is_authenticated", lambda: False)() or hasattr(client, "get_clock")):
        try:
            broker_clock = client.get_clock()
            ts = broker_clock["timestamp"]
            if isinstance(ts, str):
                ts = datetime.fromisoformat(ts)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            market_time = ts.astimezone(MARKET_TZ)
            clock_source = "ALPACA_BROKER_CLOCK"
            return local_now, market_time, market_time.date(), clock_source
        except Exception as e:
            if require_broker_clock:
                raise RuntimeError(f"BROKER_CLOCK_REQUIRED: 获取 Alpaca Broker Clock 失败，严禁依赖本地时钟执行: {e}") from e
            logger.warning(f"获取 Alpaca Broker Clock 失败，降级使用系统纽约时区: {e}")
    elif require_broker_clock:
        raise RuntimeError("BROKER_CLOCK_REQUIRED: 未提供 Broker 客户端，真实执行必须使用 Alpaca Broker Clock！")

    market_time = datetime.now(MARKET_TZ)
    return local_now, market_time, market_time.date(), clock_source

def check_execution_window(
    signal_month: str,
    signal_date: pd.Timestamp,
    market_datetime: Optional[Union[datetime, pd.Timestamp]] = None,
    client: Optional[Any] = None,
    known_trading_dates: Optional[pd.DatetimeIndex] = None,
    is_open: Optional[bool] = None,
    require_broker_clock: bool = False
) -> ExecutionWindowStatus:
    """
    严格检验当前美股市场日期与时间是否位于合法的调仓执行窗口 (Execution Window Guard)。
    
    【核心安全保证与 Timezone / Timing 规则】：
    1. 真正允许 execute 必须同时满足：
       a) Broker Calendar 确认：Current Market Date == Scheduled Execution Date
       b) Broker Clock：is_open == True
       c) 当前 America/New_York 时间必须位于：09:30:00 <= market_time < 09:35:00 ET
    2. 窗口外行为 (如 03:00, 09:20, 09:29:59, 09:35, 15:59, 16:01)：
       - Execution Allowed = False
       - Block Reason = MISSED_EXECUTION_TIME_WINDOW (严禁向 Alpaca submit，不得排队，不得 Catch-Up)
    3. 真实 execute 路径必须依赖 Alpaca get_clock()，严禁依赖系统时间/本地时区/亚洲时区决定能否提交；
    4. 禁止 Catch-Up Trading：若当前市场日期已超过计划执行日，直接判定 MISSED_REBALANCE_WINDOW 并阻断订单；
    5. 验证计划执行日是否为有效交易日 (is_exchange_trading_day 保护)。
    """
    cal_source = get_calendar_source(client)
    clock_source = "SPECIFIED_DATETIME"
    broker_is_open = None

    if client is not None and (getattr(client, "is_authenticated", lambda: False)() or hasattr(client, "get_clock")):
        try:
            broker_clock = client.get_clock()
            broker_is_open = broker_clock.get("is_open")
            if market_datetime is None:
                ts = broker_clock.get("timestamp")
                if isinstance(ts, str):
                    ts = datetime.fromisoformat(ts)
                if ts is not None:
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=timezone.utc)
                    market_datetime = ts.astimezone(MARKET_TZ)
                    clock_source = "ALPACA_BROKER_CLOCK"
        except Exception as e:
            if require_broker_clock:
                raise RuntimeError(f"BROKER_CLOCK_REQUIRED: 获取 Alpaca Broker Clock 失败: {e}") from e
            logger.warning(f"获取 Broker Clock 失败: {e}")

    if market_datetime is not None:
        if isinstance(market_datetime, pd.Timestamp):
            market_datetime = market_datetime.to_pydatetime()
        if market_datetime.tzinfo is not None:
            market_dt = market_datetime.astimezone(MARKET_TZ)
        else:
            market_dt = market_datetime.replace(tzinfo=MARKET_TZ)
        m_date = market_dt.date()
    else:
        _, market_dt, m_date, clock_source = get_market_now(client=client, require_broker_clock=require_broker_clock)

    # 确定市场是否开市状态 (is_open)
    if is_open is not None:
        market_open_val = is_open
    elif broker_is_open is not None:
        market_open_val = bool(broker_is_open)
    else:
        # 离线或纯指定时间测试模式：在常规交易时段 (09:30:00 - 16:00:00) 且为交易日时推定为开市
        market_open_val = (
            (EXECUTION_WINDOW_START <= market_dt.time() < time(16, 0, 0)) and
            is_exchange_trading_day(m_date, client=client)
        )

    # 确定信号所属月的下一个自然月
    sig_period = pd.Period(signal_month, freq="M")
    next_period = sig_period + 1

    scheduled_exec_date = get_first_trading_day_of_month(
        year=next_period.year,
        month=next_period.month,
        client=client,
        known_trading_dates=known_trading_dates
    )

    sched_d = scheduled_exec_date.date()

    # 1. 计划执行日有效性校验
    if not is_exchange_trading_day(sched_d, client=client):
        return ExecutionWindowStatus(
            signal_month=signal_month,
            signal_date=signal_date,
            scheduled_execution_date=scheduled_exec_date,
            market_datetime=market_dt,
            market_date=m_date,
            execution_allowed=False,
            execution_block_reason=f"INVALID_SCHEDULED_DATE: 计算出的调仓日 {sched_d} 不是有效交易所交易日",
            calendar_source=cal_source,
            clock_source=clock_source,
            is_market_open=market_open_val
        )

    # 2. 日期校验
    if m_date > sched_d:
        return ExecutionWindowStatus(
            signal_month=signal_month,
            signal_date=signal_date,
            scheduled_execution_date=scheduled_exec_date,
            market_datetime=market_dt,
            market_date=m_date,
            execution_allowed=False,
            execution_block_reason=f"MISSED_REBALANCE_WINDOW (计划执行日为 {sched_d}，当前市场日期已为 {m_date}。Research V1 禁止 Catch-Up Trading，等待下一周期)",
            calendar_source=cal_source,
            clock_source=clock_source,
            is_market_open=market_open_val
        )
    elif m_date < sched_d:
        return ExecutionWindowStatus(
            signal_month=signal_month,
            signal_date=signal_date,
            scheduled_execution_date=scheduled_exec_date,
            market_datetime=market_dt,
            market_date=m_date,
            execution_allowed=False,
            execution_block_reason=f"PRE_EXECUTION_WINDOW (尚未到达计划调仓执行日 {sched_d}，当前市场日期为 {m_date})",
            calendar_source=cal_source,
            clock_source=clock_source,
            is_market_open=market_open_val
        )

    # 3. 日期匹配 (m_date == sched_d)，进行严格时间窗口判定: 09:30:00 <= market_time < 09:35:00 ET
    in_time_window = (EXECUTION_WINDOW_START <= market_dt.time() < EXECUTION_WINDOW_END)
    if not in_time_window:
        return ExecutionWindowStatus(
            signal_month=signal_month,
            signal_date=signal_date,
            scheduled_execution_date=scheduled_exec_date,
            market_datetime=market_dt,
            market_date=m_date,
            execution_allowed=False,
            execution_block_reason=f"MISSED_EXECUTION_TIME_WINDOW (当前市场时间 {market_dt.strftime('%H:%M:%S')} ET 不在允许执行窗口 09:30:00 - 09:35:00 ET 内，严禁向 Alpaca 提交订单或排队)",
            calendar_source=cal_source,
            clock_source=clock_source,
            is_market_open=market_open_val
        )

    # 4. 校验 Broker Clock 权威状态: is_open == True
    if not market_open_val:
        return ExecutionWindowStatus(
            signal_month=signal_month,
            signal_date=signal_date,
            scheduled_execution_date=scheduled_exec_date,
            market_datetime=market_dt,
            market_date=m_date,
            execution_allowed=False,
            execution_block_reason="MISSED_EXECUTION_TIME_WINDOW (Broker Clock 确认市场未开市 is_open=False，严禁报单)",
            calendar_source=cal_source,
            clock_source=clock_source,
            is_market_open=market_open_val
        )

    return ExecutionWindowStatus(
        signal_month=signal_month,
        signal_date=signal_date,
        scheduled_execution_date=scheduled_exec_date,
        market_datetime=market_dt,
        market_date=m_date,
        execution_allowed=True,
        execution_block_reason="NONE (ON_SCHEDULE)",
        calendar_source=cal_source,
        clock_source=clock_source,
        is_market_open=market_open_val
    )
