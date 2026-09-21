import os
import pandas as pd
import numpy as np
import yfinance as yf
from pathlib import Path
from typing import Dict, List, Tuple, Optional, NamedTuple
from config.config import DATA_CACHE_DIR, UNIVERSE
from src.data.validator import validate_single_etf_data, align_universe_data
from src.utils.logger import logger

class RebalanceEvent(NamedTuple):
    """调仓事件的显式时间线映射元组"""
    period: str                  # 信号所属自然月 (e.g. '2025-12')
    signal_date: pd.Timestamp    # 信号生成日期 (该月最后一个有效交易日收盘后)
    execution_date: pd.Timestamp # 调仓执行日期 (下一个月首个有效交易日开盘)

def calculate_adjusted_ohlc(df: pd.DataFrame) -> pd.DataFrame:
    """
    计算基于复权因子 (adj_close / close) 的调整 OHLC 价格序列。
    
    【重要说明】：
    df['adj_open'] 是回测专用的复权执行价格代理 (Backtest Adjusted Execution Price Proxy)，
    目的是保证回测在跨除权、除息和拆股周期时，开盘成交与收盘估值的现金流调整一致性。
    这并非现实交易中当日交易所挂牌的未经调整的原始开盘价。
    在 Paper Trading 与实盘阶段，订单将以 Alpaca 实时市场价和真实成交结果为准。
    """
    df = df.copy()
    # 转换为小写标准列名
    df.columns = [c.lower().replace(" ", "_") for c in df.columns]
    
    if "adj_close" not in df.columns:
        if "close" in df.columns:
            df["adj_close"] = df["close"]
        else:
            raise KeyError("数据中缺少 close 或 adj_close 价格列。")

    # 计算调整因子 (Dividend and Split Adjustment Factor)
    adj_factor = df["adj_close"] / df["close"]
    
    # 生成复权代理价格序列
    df["adj_open"] = df["open"] * adj_factor
    df["adj_high"] = df["high"] * adj_factor
    df["adj_low"] = df["low"] * adj_factor
    
    return df

def fetch_single_etf(
    symbol: str, 
    start_date: str = "2005-01-01", 
    end_date: Optional[str] = None, 
    force_refresh: bool = False
) -> pd.DataFrame:
    """
    获取单只 ETF 的历史日线行情数据。
    优先从本地缓存 data/cache/{symbol}.csv 读取，若不存在或 force_refresh=True 则从 yfinance 下载。
    """
    cache_file = DATA_CACHE_DIR / f"{symbol}.csv"

    if cache_file.exists() and not force_refresh:
        logger.debug(f"[{symbol}] 从本地缓存读取: {cache_file}")
        df = pd.read_csv(cache_file, index_col=0, parse_dates=True)
        # 兼容时区信息，统一转为无时区日度时间
        if df.index.tz is not None:
            df.index = df.index.tz_localize(None)
        validate_single_etf_data(df, symbol)
        return df

    logger.info(f"[{symbol}] 本地缓存未命中或强制刷新，正在从 Yahoo Finance 下载历史行情 ({start_date} -> {end_date or 'latest'})...")
    data = yf.download(symbol, start=start_date, end=end_date, auto_adjust=False, progress=False)

    if data.empty:
        raise ValueError(f"[{symbol}] 未能从 Yahoo Finance 下载到任何行情数据，请检查网络或代码有效性。")

    if isinstance(data.columns, pd.MultiIndex):
        data.columns = data.columns.get_level_values(0)

    df = calculate_adjusted_ohlc(data)
    standard_cols = ["open", "high", "low", "close", "adj_close", "adj_open", "volume"]
    df = df[standard_cols].copy()
    
    if df.index.tz is not None:
        df.index = df.index.tz_localize(None)
    
    df = df[~df.index.duplicated(keep="first")].sort_index()
    validate_single_etf_data(df, symbol)
    df.to_csv(cache_file)
    logger.info(f"[{symbol}] 数据已成功验证并持久化至本地缓存: {cache_file} (共 {len(df)} 交易日)")
    return df

def fetch_universe_data(
    symbols: List[str] = UNIVERSE, 
    start_date: str = "2005-01-01", 
    end_date: Optional[str] = None, 
    force_refresh: bool = False
) -> Dict[str, pd.DataFrame]:
    """
    批量获取并严格对齐资产池全部 ETF 的日线数据。
    """
    raw_dict = {}
    for sym in symbols:
        raw_dict[sym] = fetch_single_etf(sym, start_date=start_date, end_date=end_date, force_refresh=force_refresh)
    
    # 严格交易日对齐（含前置独立缺口审查）
    aligned_dict = align_universe_data(raw_dict)
    return aligned_dict

def is_month_completed(
    year_month: pd.Period, 
    max_data_date: pd.Timestamp, 
    as_of_date: Optional[pd.Timestamp] = None
) -> bool:
    """
    【Phase 3.5 核心】：严格判定某自然月份是否已完整结束 (Completed Month)。
    
    规则：
    1. 若数据集中存在该月之后的交易日 (max_data_date.to_period('M') > year_month)，该月必定已完整闭盘。
    2. 若该月是数据集中最晚的一个月份 (max_data_date.to_period('M') == year_month)：
       - 若当前运行日期或参考日期处于该月内部 (as_of_date.to_period('M') == year_month)，说明该月尚未走完，判定为 False (Partial Month)。
       - 若数据最晚日期距当月日历月末超过 1 个工作日未覆盖，判定为月中截断数据，返回 False。
       - 否则仅当该月最后一个交易日已完成且跨月后才判定为 True。
    """
    # 1. 存在后继月份数据
    if max_data_date.to_period("M") > year_month:
        return True

    # 2. 检查参考日期 (as_of_date 或当前本地/系统日期)
    ref_date = as_of_date if as_of_date is not None else pd.Timestamp.now()
    if ref_date.tz is not None:
        ref_date = ref_date.tz_localize(None)

    # 若参考时间处于当前自然月内，该自然月尚未结束
    if ref_date.to_period("M") == year_month:
        return False

    # 3. 若参考时间已在未来，但该月数据在月中截断 (如截止在 15 号)
    cal_month_end = year_month.end_time.date()
    # 检查 max_data_date 到月末之间是否还有剩余工作日
    remaining_weekdays = pd.bdate_range(max_data_date + pd.Timedelta(days=1), cal_month_end)
    if len(remaining_weekdays) > 1:
        return False

    return True

def get_monthly_dataset(
    aligned_dict: Dict[str, pd.DataFrame],
    completed_month_only: bool = True,
    as_of_date: Optional[pd.Timestamp] = None
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """
    根据日线行情提取月度信号与执行价格矩阵，严格支持仅包含已完成月份 (completed_month_only)。
    
    返回:
       (monthly_signal_close, monthly_execution_open)
       - monthly_signal_close: 索引为各完整月份最后一个交易日
       - monthly_execution_open: 索引为各完整月份首个交易日
    """
    sample_df = next(iter(aligned_dict.values()))
    if as_of_date is not None:
        if as_of_date.tz is not None:
            as_of_date = as_of_date.tz_localize(None)
        sample_df = sample_df[sample_df.index <= as_of_date]

    dates = sample_df.index
    max_data_date = dates.max()

    df_dates = pd.DataFrame({"date": dates}, index=dates)
    df_dates["year_month"] = df_dates["date"].dt.to_period("M")

    # 过滤未完成月份
    all_months = sorted(list(df_dates["year_month"].unique()))
    valid_months = []
    for ym in all_months:
        if completed_month_only:
            if is_month_completed(ym, max_data_date=max_data_date, as_of_date=as_of_date):
                valid_months.append(ym)
            else:
                logger.info(f"剔除未完成的自然月 (Partial/Incomplete Month): {ym}")
        else:
            valid_months.append(ym)

    if not valid_months:
        raise ValueError("经完整月份筛选后，没有可用的月度数据！")

    # 仅保留已完成月份的数据
    df_dates_valid = df_dates[df_dates["year_month"].isin(valid_months)]
    month_ends = df_dates_valid.groupby("year_month")["date"].max()
    month_starts = df_dates_valid.groupby("year_month")["date"].min()

    close_data = {}
    open_data = {}
    for sym, df in aligned_dict.items():
        sub_df = df[df.index <= as_of_date] if as_of_date is not None else df
        close_data[sym] = sub_df.loc[month_ends, "adj_close"]
        open_data[sym] = sub_df.loc[month_starts, "adj_open"]

    monthly_signal_close = pd.DataFrame(close_data, index=month_ends)
    monthly_execution_open = pd.DataFrame(open_data, index=month_starts)
    
    return monthly_signal_close, monthly_execution_open

def build_rebalance_schedule(
    aligned_dict: Dict[str, pd.DataFrame],
    as_of_date: Optional[pd.Timestamp] = None
) -> List[RebalanceEvent]:
    """
    【显式时间线映射】：构建严格的 (signal_date, execution_date) 调仓周期日程表。
    
    逻辑：
    - 对于每个已完整闭盘的自然月 m：
      signal_date: 月 m 最后一个有效交易日收盘后 (生成信号)
      execution_date: 下一自然月 m+1 首个有效交易日开盘 (执行成交)
    - 绝不允许行号隐式对应，严格断言 execution_date > signal_date。
    """
    sample_df = next(iter(aligned_dict.values()))
    if as_of_date is not None:
        sample_df = sample_df[sample_df.index <= as_of_date]

    dates = sample_df.index
    df_dates = pd.DataFrame({"date": dates}, index=dates)
    df_dates["year_month"] = df_dates["date"].dt.to_period("M")

    all_months = sorted(list(df_dates["year_month"].unique()))
    schedule: List[RebalanceEvent] = []

    for i in range(len(all_months) - 1):
        m_curr = all_months[i]
        m_next = all_months[i + 1]

        # m_curr 的最后交易日
        sig_date = df_dates[df_dates["year_month"] == m_curr]["date"].max()
        # m_next 的首个交易日
        exec_date = df_dates[df_dates["year_month"] == m_next]["date"].min()

        assert exec_date > sig_date, f"调仓时间线反转错误: execution_date {exec_date} <= signal_date {sig_date}"
        schedule.append(RebalanceEvent(
            period=str(m_curr),
            signal_date=sig_date,
            execution_date=exec_date
        ))

    logger.info(
        f"构建显式调仓时间线日程表: 共 {len(schedule)} 个完整调仓周期 "
        f"({schedule[0].period} -> {schedule[-1].period})"
    )
    return schedule
