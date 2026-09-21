import pytest
import pandas as pd
import numpy as np
from src.signals.trend import calculate_10m_sma, check_trend_filter

def test_trend_filter_basic_calculation():
    """
    测试 10-Month SMA 基础计算正确性。
    """
    dates = pd.date_range("2025-01-31", periods=12, freq="ME")
    # 构造过去10个月均值已知的序列
    # 前 2 个月随便，后 10 个月为 100 到 109
    prices = [50.0, 60.0] + [100.0 + i for i in range(10)]
    df = pd.DataFrame({"ETF1": prices}, index=dates)

    sma_df = calculate_10m_sma(df, window_months=10)
    
    # 最后一个点对应最后 10 个价格的算术平均: (100 + 101 + ... + 109) / 10 = 104.5
    expected_sma = sum([100.0 + i for i in range(10)]) / 10.0
    actual_sma = sma_df.loc[dates[-1], "ETF1"]
    assert pytest.approx(actual_sma, abs=1e-8) == expected_sma

def test_trend_filter_allows_recent_month_and_can_change():
    """
    【特别测试 4】：
    证明：对于 2026-01 调仓，Trend Filter 允许使用 2025-12 已完成月末价格。
    修改 2025-12 价格后，Trend Filter 状态应当且允许改变。
    这与 Momentum 测试中的“2025-12 修改保持不变”严格形成对比，符合策略设计规范。
    """
    dates = pd.date_range("2025-01-31", periods=12, freq="ME")
    # 假设前 11 个月价格平稳在 100
    prices = [100.0] * 12
    base_df = pd.DataFrame({"ETF1": prices}, index=dates)

    # 场景 A: 2025-12 价格大幅上涨至 120 (明显高于 10M SMA)，应通过过滤 (True)
    df_bull = base_df.copy()
    df_bull.loc[dates[-1], "ETF1"] = 120.0
    passed_bull = check_trend_filter(df_bull).loc[dates[-1], "ETF1"]
    assert passed_bull == True, "最新价格高于 10M SMA 应通过趋势过滤！"

    # 场景 B: 2025-12 价格大幅下跌至 80 (明显跌破 10M SMA)，应被淘汰 (False)
    df_bear = base_df.copy()
    df_bear.loc[dates[-1], "ETF1"] = 80.0
    passed_bear = check_trend_filter(df_bear).loc[dates[-1], "ETF1"]
    assert passed_bear == False, "最新价格跌破 10M SMA 必须被淘汰！"

    # 场景 C: 价格严格等于 10M SMA 时判定为 False (必须严格大于)
    df_equal = base_df.copy()
    # 使得最新价格正好等于过去 10 个月的均值 (即全是 100)
    passed_equal = check_trend_filter(df_equal).loc[dates[-1], "ETF1"]
    assert passed_equal == False, "价格等于 10M SMA 时判定为未突破趋势，应为 False"

def test_trend_filter_insufficient_history():
    """
    测试数据不足 10 个月时的保护：不能报错，且必须返回 False。
    """
    dates = pd.date_range("2025-01-31", periods=5, freq="ME")
    df = pd.DataFrame({"ETF1": [100.0] * 5}, index=dates)

    passed = check_trend_filter(df)
    assert (passed["ETF1"] == False).all()
