import pandas as pd
import numpy as np
from config.config import MOMENTUM_LOOKBACK_MONTHS, MOMENTUM_SKIP_MONTHS

def calculate_12_1_momentum(
    monthly_prices: pd.DataFrame,
    lookback_months: int = MOMENTUM_LOOKBACK_MONTHS,
    skip_months: int = MOMENTUM_SKIP_MONTHS
) -> pd.DataFrame:
    """
    计算经典 12-1 动量 (12-1 Momentum)，严格跳过最近一个完整月份。
    
    【核心数学与时间对齐定义】：
    假设 monthly_prices 的行索引为各自然月最后一个交易日 (按时间升序排列)。
    若调仓发生在 2026 年 1 月第 1 个交易日，此时已获得的完整月末序列最新一行为 2025-12 (索引 t)：
    - 2025-12 是最近一个完整月份，必须完全跳过；
    - 动量计算截止到 2025-11 月末 (索引 t - 1)；
    - 动量计算起始点为 2024-12 月末 (索引 t - 12)；
    - 动量收益公式：(P_{2025-11} / P_{2024-12}) - 1。
    
    向量化实现：
    分子: monthly_prices.shift(skip_months) -> 对应 t - 1 (2025-11)
    分母: monthly_prices.shift(lookback_months) -> 对应 t - 12 (2024-12)
    动量 = 分子 / 分母 - 1
    
    参数:
        monthly_prices: 资产池各 ETF 的月末复权收盘价矩阵 (DataFrame, index=月末交易日, columns=ETF代码)
        lookback_months: 回溯月数 (默认 12)
        skip_months: 跳过月数 (默认 1)
        
    返回:
        DataFrame: 各 ETF 对应的 12-1 动量序列。
    """
    if len(monthly_prices) < lookback_months + 1:
        # 数据长度不足以计算
        return pd.DataFrame(np.nan, index=monthly_prices.index, columns=monthly_prices.columns)

    numerator = monthly_prices.shift(skip_months)
    denominator = monthly_prices.shift(lookback_months)
    
    # 防止除以 0 或负数
    valid_mask = (denominator > 0) & (numerator > 0)
    momentum = (numerator / denominator) - 1.0
    momentum[~valid_mask] = np.nan

    return momentum
