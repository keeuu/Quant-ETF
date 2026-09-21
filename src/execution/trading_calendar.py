from typing import Optional, List, Dict, Any, Union
from datetime import datetime, date
import pandas as pd
import exchange_calendars as xcals
from src.utils.logger import logger

# 全局单例初始化 XNYS (NYSE) 官方市场日历
_XNYS_CAL = xcals.get_calendar("XNYS")

def get_xnys_calendar() -> xcals.ExchangeCalendar:
    """获取底层 exchange_calendars XNYS 官方日历实例"""
    return _XNYS_CAL

def is_exchange_trading_day(
    dt: Union[pd.Timestamp, datetime, date, str],
    client: Optional[Any] = None
) -> bool:
    """
    检查指定日期是否为合法的美国股票交易日 (Session)。
    
    规则优先级：
    1. 【权威数据源】在 Real Alpaca Paper 模式下，优先查询 Alpaca Broker 市场日历；
    2. 【标准离线日历】使用 exchange_calendars (XNYS) 官方日历验证，精确处理 Good Friday、
       全美假日及历史特殊休市，不依赖任何手写假日列表。
    """
    if isinstance(dt, str):
        check_date = pd.Timestamp(dt).date()
    elif isinstance(dt, (datetime, pd.Timestamp)):
        check_date = dt.date()
    else:
        check_date = dt

    # 1. Real Alpaca Paper 模式：Alpaca Broker Calendar 是最高权威
    if client is not None and getattr(client, "is_authenticated", lambda: False)():
        try:
            date_str = check_date.strftime("%Y-%m-%d")
            cal_days = client.get_calendar(start_date=date_str, end_date=date_str)
            return len(cal_days) > 0
        except Exception as e:
            logger.warning(f"从 Alpaca 获取交易日历失败，回退至 exchange_calendars XNYS: {e}")

    # 2. 离线/测试/降级：使用 exchange_calendars XNYS
    ts = pd.Timestamp(check_date)
    return bool(_XNYS_CAL.is_session(ts))

def get_first_trading_day_of_month(
    year: int,
    month: int,
    client: Optional[Any] = None,
    known_trading_dates: Optional[pd.DatetimeIndex] = None
) -> pd.Timestamp:
    """
    计算某年某月首个有效美国股票交易日。
    
    规则优先级：
    1. 【权威数据源】Alpaca Broker Market Calendar (在线实盘/模拟唯一权威)；
    2. 【历史对齐】历史回测/研究公共交易日 known_trading_dates；
    3. 【离线标准】exchange_calendars XNYS 官方交易日历序列。
    """
    start_str = f"{year:04d}-{month:02d}-01"
    end_str = f"{year:04d}-{month:02d}-15"

    # 优先级 1: Alpaca Broker 市场日历
    if client is not None and getattr(client, "is_authenticated", lambda: False)():
        try:
            cal_days = client.get_calendar(start_date=start_str, end_date=end_str)
            if cal_days:
                first_date = cal_days[0]["date"]
                if isinstance(first_date, (datetime, pd.Timestamp)):
                    return pd.Timestamp(first_date.date())
                elif isinstance(first_date, date):
                    return pd.Timestamp(first_date)
                elif isinstance(first_date, str):
                    return pd.Timestamp(first_date)
        except Exception as e:
            logger.warning(f"调用 Alpaca get_calendar 失败，降级至 exchange_calendars: {e}")

    # 优先级 2: 历史对齐交易日集合
    if known_trading_dates is not None and len(known_trading_dates) > 0:
        matches = known_trading_dates[(known_trading_dates.year == year) & (known_trading_dates.month == month)]
        if not matches.empty:
            return matches[0]

    # 优先级 3: exchange_calendars XNYS 官方日历
    sessions = _XNYS_CAL.sessions_in_range(start_str, end_str)
    if len(sessions) > 0:
        return pd.Timestamp(sessions[0].date())

    raise RuntimeError(f"未能计算出 {year}-{month:02d} 的首个交易日")

def is_early_close(
    dt: Union[pd.Timestamp, datetime, date, str]
) -> bool:
    """检查指定日期是否为 NYSE 提前收盘日 (Early Close, 如黑五、7月3日等)"""
    ts = pd.Timestamp(dt).normalize()
    return ts in _XNYS_CAL.early_closes

def get_calendar_source(client: Optional[Any] = None) -> str:
    """返回当前交易日历判定所依赖的数据源标识"""
    if client is not None and getattr(client, "is_authenticated", lambda: False)():
        return "ALPACA_BROKER_CALENDAR"
    return "EXCHANGE_CALENDARS_XNYS"
