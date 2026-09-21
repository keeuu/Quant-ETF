import pytest
import pandas as pd
import numpy as np
from src.signals.momentum import calculate_12_1_momentum

def test_momentum_exact_calculation():
    """
    测试 12-1 动量的核心数学逻辑精确性。
    构造 2024-12 到 2025-12 完整 13 个月的序列。
    对于 2026-01 调仓 (即最新已收盘月末为 2025-12-31)：
    动量必须严格等于: P_{2025-11} / P_{2024-12} - 1。
    """
    dates = pd.date_range("2024-12-31", periods=13, freq="ME")
    # 模拟价格序列: 100, 101, ..., 111 (2025-11), 200 (2025-12)
    # 2024-12-31 为 100
    # 2025-11-30 为 120
    # 2025-12-31 为 999 (极端扰动值)
    prices = [100.0] + [100.0 + i * 2 for i in range(1, 11)] + [120.0, 999.0]
    df = pd.DataFrame({"ETF1": prices}, index=dates)

    mom_df = calculate_12_1_momentum(df, lookback_months=12, skip_months=1)
    
    # 最后一期对应 2025-12-31 闭盘后的动量计算结果
    latest_mom = mom_df.loc[dates[-1], "ETF1"]
    
    # 理论预期: (P_{2025-11} / P_{2024-12}) - 1 = (120.0 / 100.0) - 1 = 0.20 (20%)
    expected_mom = (120.0 / 100.0) - 1.0
    assert pytest.approx(latest_mom, abs=1e-8) == expected_mom

def test_momentum_invariance_on_recent_month_modification():
    """
    【特别测试 1】：修改 2025-12 价格，12-1 Momentum 必须保持完全不变！
    证明 2025-12 (最近一个完整月) 收益被严格跳过，杜绝进入动量计算。
    """
    dates = pd.date_range("2024-12-31", periods=13, freq="ME")
    base_prices = [100.0 + i * 2 for i in range(13)]
    base_df = pd.DataFrame({"ETF1": base_prices}, index=dates)
    
    # 基准动量
    base_mom = calculate_12_1_momentum(base_df).loc[dates[-1], "ETF1"]

    # 极端修改 2025-12 的价格 (暴增 1000% 或暴跌 90%)
    modified_df_high = base_df.copy()
    modified_df_high.loc[dates[-1], "ETF1"] = 10000.0

    modified_df_low = base_df.copy()
    modified_df_low.loc[dates[-1], "ETF1"] = 1.0

    mom_high = calculate_12_1_momentum(modified_df_high).loc[dates[-1], "ETF1"]
    mom_low = calculate_12_1_momentum(modified_df_low).loc[dates[-1], "ETF1"]

    # 断言必须绝对相同
    assert mom_high == pytest.approx(base_mom, abs=1e-12), "修改 2025-12 价格后动量发生了改变，违反跳过最近月规则！"
    assert mom_low == pytest.approx(base_mom, abs=1e-12), "修改 2025-12 价格后动量发生了改变，违反跳过最近月规则！"

def test_momentum_sensitivity_on_cutoff_month_modification():
    """
    【特别测试 2】：修改 2025-11 价格，12-1 Momentum 必须立刻发生改变！
    证明 2025-11 确实作为收益计算的分子端准确生效。
    """
    dates = pd.date_range("2024-12-31", periods=13, freq="ME")
    base_prices = [100.0 + i * 2 for i in range(13)]
    base_df = pd.DataFrame({"ETF1": base_prices}, index=dates)
    
    base_mom = calculate_12_1_momentum(base_df).loc[dates[-1], "ETF1"]

    # 修改 2025-11-30 的价格
    modified_df = base_df.copy()
    cutoff_date = dates[-2] # 2025-11
    modified_df.loc[cutoff_date, "ETF1"] = modified_df.loc[cutoff_date, "ETF1"] * 1.5

    modified_mom = calculate_12_1_momentum(modified_df).loc[dates[-1], "ETF1"]

    # 断言必须改变
    assert modified_mom != pytest.approx(base_mom, abs=1e-5), "修改 2025-11 价格后动量未发生改变！"
    # 且新的动量严格等于新分子 / 分母 - 1
    expected_new_mom = (base_df.loc[cutoff_date, "ETF1"] * 1.5 / base_df.loc[dates[0], "ETF1"]) - 1.0
    assert pytest.approx(modified_mom, abs=1e-8) == expected_new_mom

def test_momentum_future_data_invariance():
    """
    【特别测试 3】：增加或修改 2026-01 及未来价格，历史 2025-12 闭盘时点的动量绝对不受影响。
    """
    dates = pd.date_range("2024-12-31", periods=13, freq="ME")
    base_df = pd.DataFrame({"ETF1": [100.0 + i * 2 for i in range(13)]}, index=dates)
    base_mom = calculate_12_1_momentum(base_df).loc[dates[-1], "ETF1"]

    # 追加未来 2026-01 的数据
    future_date = pd.to_datetime("2026-01-31")
    extended_df = base_df.copy()
    extended_df.loc[future_date] = [9999.0]

    extended_mom = calculate_12_1_momentum(extended_df).loc[dates[-1], "ETF1"]
    assert extended_mom == pytest.approx(base_mom, abs=1e-12), "未来月份的数据泄漏影响了历史动量计算！"
