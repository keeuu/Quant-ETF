import pandas as pd
import numpy as np
from config.config import TREND_SMA_MONTHS

def calculate_10m_sma(
    monthly_prices: pd.DataFrame,
    window_months: int = TREND_SMA_MONTHS
) -> pd.DataFrame:
    r"""
    计算 10 个月移动平均线 (10-Month SMA)。
    
    【核心数学与时间对齐定义】：
    以调仓发生在 2026 年 1 月第 1 个交易日为例：
    截至调仓前夕已完整收盘的最新月份为 2025-12 (索引 t)。
    10M SMA 包含 2025-03 至 2025-12 共 10 个已完成月末的复权收盘价均值：
        SMA_10(t) = (1 / 10) * \sum_{k=0}^{9} P_{t-k}
    """
    if len(monthly_prices) < window_months:
        return pd.DataFrame(np.nan, index=monthly_prices.index, columns=monthly_prices.columns)
    
    return monthly_prices.rolling(window=window_months).mean()

def check_trend_filter(
    monthly_prices: pd.DataFrame,
    window_months: int = TREND_SMA_MONTHS
) -> pd.DataFrame:
    """
    判断资产是否通过 10 个月移动平均线趋势过滤。
    
    规则：
    只有当最新已收盘月末价格 P_t 严格大于其 10M SMA 时才算通过：
        Passed = (P_t > SMA_10(t))
    若 P_t <= SMA_10(t) 或数据不足 10 个月，则判定为 False。
    
    返回:
        DataFrame: 布尔矩阵，True 代表通过趋势过滤，具备持仓资格。
    """
    sma = calculate_10m_sma(monthly_prices, window_months=window_months)
    # 严格大于，且排除 NaN
    passed = (monthly_prices > sma) & (~sma.isna()) & (~monthly_prices.isna())
    return passed
