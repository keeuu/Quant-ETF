import pytest
import pandas as pd
import numpy as np
from src.data.fetcher import get_monthly_dataset, is_month_completed
from src.data.validator import pre_alignment_validation, DataValidationError
from src.signals.momentum import calculate_12_1_momentum
from src.signals.trend import check_trend_filter

def make_dummy_df(dates, start_price=100.0, step=1.0):
    prices = [start_price + i * step for i in range(len(dates))]
    return pd.DataFrame({
        "open": prices,
        "high": [p + 1.0 for p in prices],
        "low": [p - 1.0 for p in prices],
        "close": prices,
        "adj_close": prices,
        "adj_open": prices,
        "volume": [1000] * len(dates)
    }, index=pd.DatetimeIndex(dates))

def test_incomplete_month_excluded():
    """
    【Phase 3.5 核心测试 1】：
    构造 2025-12 完整数据 + 2026-01-01 至 2026-01-15 部分数据。
    假设 as_of_date = 2026-01-15。
    断言结果中最新 monthly close 必须是 2025-12，2026-01 绝不出现！
    """
    dates_dec = pd.date_range("2025-12-01", "2025-12-31", freq="B")
    dates_jan_partial = pd.date_range("2026-01-02", "2026-01-15", freq="B")
    all_dates = dates_dec.append(dates_jan_partial)

    data_dict = {"ETF1": make_dummy_df(all_dates)}
    as_of = pd.Timestamp("2026-01-15")

    monthly_close, _ = get_monthly_dataset(data_dict, completed_month_only=True, as_of_date=as_of)

    # 验证最新月份必须是 2025-12
    latest_month = monthly_close.index[-1].to_period("M")
    assert str(latest_month) == "2025-12", f"未完成月未被剔除，当前最新月为: {latest_month}"
    # 2026-01 不得出现
    all_periods = [str(d.to_period("M")) for d in monthly_close.index]
    assert "2026-01" not in all_periods, "未完成的 2026-01 部分月份错误地进入了月度收盘数据集！"

def test_completed_month_included():
    """
    【Phase 3.5 核心测试 2】：
    如果数据已经覆盖一个完整自然月最后有效交易日，且后续已有新月数据：
    该月应正常进入 monthly dataset。
    """
    dates_nov = pd.date_range("2025-11-01", "2025-11-30", freq="B")
    dates_dec = pd.date_range("2025-12-01", "2025-12-31", freq="B")
    dates_jan_start = pd.date_range("2026-01-02", "2026-01-05", freq="B")
    all_dates = dates_nov.append(dates_dec).append(dates_jan_start)

    data_dict = {"ETF1": make_dummy_df(all_dates)}
    # 当跨入 2026-01 后，2025-12 必定作为 completed month 被收录
    monthly_close, _ = get_monthly_dataset(data_dict, completed_month_only=True)
    all_periods = [str(d.to_period("M")) for d in monthly_close.index]
    assert "2025-11" in all_periods
    assert "2025-12" in all_periods

def test_partial_month_does_not_change_signal():
    """
    【Phase 3.5 核心测试 3】：
    在当前未完成月份中人为增加 +100%、-90% 的极端价格，
    历史已完成月份生成的 Momentum 与 Trend 信号均不得改变！
    """
    # 构造 14 个完整月份 (2024-11 至 2025-12)
    dates_full = pd.date_range("2024-11-01", "2025-12-31", freq="B")
    # 加上 2026-01 部分月份 (截止 15 号)
    dates_partial = pd.date_range("2026-01-02", "2026-01-15", freq="B")
    all_dates = dates_full.append(dates_partial)

    # 1. 基础场景
    base_df = make_dummy_df(all_dates, start_price=100.0, step=0.5)
    base_close, _ = get_monthly_dataset({"ETF1": base_df}, completed_month_only=True, as_of_date=pd.Timestamp("2026-01-15"))
    base_mom = calculate_12_1_momentum(base_close).loc[base_close.index[-1], "ETF1"]
    base_trend = check_trend_filter(base_close).loc[base_close.index[-1], "ETF1"]

    # 2. 扰动场景 A: 在未完成的 2026-01-15 价格暴涨 200%
    df_spike = base_df.copy()
    df_spike.loc[dates_partial[-1], ["open", "high", "low", "close", "adj_close", "adj_open"]] *= 3.0
    spike_close, _ = get_monthly_dataset({"ETF1": df_spike}, completed_month_only=True, as_of_date=pd.Timestamp("2026-01-15"))
    spike_mom = calculate_12_1_momentum(spike_close).loc[spike_close.index[-1], "ETF1"]
    spike_trend = check_trend_filter(spike_close).loc[spike_close.index[-1], "ETF1"]

    # 3. 扰动场景 B: 在未完成的 2026-01-15 价格暴跌 90%
    df_crash = base_df.copy()
    df_crash.loc[dates_partial[-1], ["open", "high", "low", "close", "adj_close", "adj_open"]] *= 0.1
    crash_close, _ = get_monthly_dataset({"ETF1": df_crash}, completed_month_only=True, as_of_date=pd.Timestamp("2026-01-15"))
    crash_mom = calculate_12_1_momentum(crash_close).loc[crash_close.index[-1], "ETF1"]
    crash_trend = check_trend_filter(crash_close).loc[crash_close.index[-1], "ETF1"]

    # 断言：信号绝对无任何波动！
    assert spike_mom == pytest.approx(base_mom, abs=1e-12)
    assert crash_mom == pytest.approx(base_mom, abs=1e-12)
    assert spike_trend == base_trend
    assert crash_trend == base_trend

def test_pre_alignment_detects_gap():
    """
    【Phase 3.5 核心测试 4】：
    验证 pre_alignment_validation 能够明确检测出某只 ETF 相比交易日并集的单边缺口，
    绝不会被 intersection 静默抹平。
    """
    dates_normal = pd.date_range("2025-01-01", "2025-01-10", freq="B")
    # ETF2 漏掉了 2025-01-06 和 2025-01-07 两个交易日
    dates_gap = dates_normal.drop([pd.Timestamp("2025-01-06"), pd.Timestamp("2025-01-07")])

    raw_dict = {
        "SPY": make_dummy_df(dates_normal),
        "EEM": make_dummy_df(dates_gap)
    }

    report = pre_alignment_validation(raw_dict)
    assert len(report["EEM"]["missing_dates"]) == 2
    assert pd.Timestamp("2025-01-06") in report["EEM"]["missing_dates"]
    assert pd.Timestamp("2025-01-07") in report["EEM"]["missing_dates"]
    assert len(report["SPY"]["missing_dates"]) == 0
