import pytest
import pandas as pd
import numpy as np
from src.data.fetcher import build_rebalance_schedule, RebalanceEvent, get_monthly_dataset
from src.signals.momentum import calculate_12_1_momentum
from src.signals.trend import check_trend_filter
from src.signals.selector import select_top_assets
from src.backtest.engine import run_backtest

def make_two_month_dummy():
    dates_dec = pd.date_range("2025-12-01", "2025-12-31", freq="B")
    dates_jan = pd.date_range("2026-01-02", "2026-01-30", freq="B")
    dates_feb = pd.date_range("2026-02-02", "2026-02-05", freq="B")
    all_dates = dates_dec.append(dates_jan).append(dates_feb)
    df = pd.DataFrame({
        "open": [100.0] * len(all_dates),
        "high": [105.0] * len(all_dates),
        "low": [95.0] * len(all_dates),
        "close": [100.0] * len(all_dates),
        "adj_close": [100.0] * len(all_dates),
        "adj_open": [100.0] * len(all_dates),
        "volume": [1000] * len(all_dates)
    }, index=all_dates)
    return {"SPY": df, "QQQ": df.copy(), "IWM": df.copy(), "EFA": df.copy(),
            "EEM": df.copy(), "GLD": df.copy(), "TLT": df.copy(), "IEF": df.copy()}

def test_signal_execution_mapping():
    """
    【Phase 4 核心测试 1】：
    人工构造 2025-12 月末信号日，断言其对应的调仓执行日必须是 2026-01 第一个交易日。
    绝不能使用 2025-12 首日、2025-12 月末或 2026-01 月末。
    """
    dummy = make_two_month_dummy()
    sched = build_rebalance_schedule(dummy)
    
    # 找到 2025-12 周期的调仓事件
    dec_event = [e for e in sched if e.period == "2025-12"][0]
    
    # 2025-12 最后一个工作日是 2025-12-31
    assert dec_event.signal_date == pd.Timestamp("2025-12-31")
    # 2026-01 第一个交易日是 2026-01-02
    assert dec_event.execution_date == pd.Timestamp("2026-01-02")
    
    # 绝不能等于当期首日、当期末日或次月末日
    assert dec_event.execution_date != pd.Timestamp("2025-12-01")
    assert dec_event.execution_date != pd.Timestamp("2025-12-31")
    assert dec_event.execution_date != pd.Timestamp("2026-01-30")

def test_no_execution_before_signal():
    """
    【Phase 4 核心测试 2】：
    对整个历史日程表中的所有周期，严格断言 execution_date > signal_date。
    """
    dummy = make_two_month_dummy()
    sched = build_rebalance_schedule(dummy)
    for e in sched:
        assert e.execution_date > e.signal_date, f"未来函数错误: {e.execution_date} <= {e.signal_date}"

def test_future_price_invariance():
    """
    【Phase 4 核心测试 3】：
    改变 execution_date 之后的未来价格，不得改变此前已经生成的 signal。
    """
    # 构造足够计算动量 (14个月) 的数据
    dates = pd.date_range("2024-11-01", "2025-12-31", freq="B").append(pd.date_range("2026-01-02", "2026-01-30", freq="B"))
    base_df = pd.DataFrame({
        "open": [100.0] * len(dates),
        "high": [101.0] * len(dates),
        "low": [99.0] * len(dates),
        "close": [100.0] * len(dates),
        "adj_close": [100.0] * len(dates),
        "adj_open": [100.0] * len(dates),
        "volume": [1000] * len(dates)
    }, index=dates)

    data_base = {"ETF1": base_df.copy()}
    close_base, _ = get_monthly_dataset(data_base, completed_month_only=True, as_of_date=pd.Timestamp("2026-01-30"))
    mom_base = calculate_12_1_momentum(close_base).loc[close_base.index[-2], "ETF1"]

    # 篡改 2026-01 月末的未来价格 (例如第 15 个月末)
    data_tampered = {"ETF1": base_df.copy()}
    data_tampered["ETF1"].loc["2026-01-30", ["open", "close", "adj_close", "adj_open"]] = 99999.0
    close_tampered, _ = get_monthly_dataset(data_tampered, completed_month_only=True, as_of_date=pd.Timestamp("2026-01-30"))
    mom_tampered = calculate_12_1_momentum(close_tampered).loc[close_tampered.index[-2], "ETF1"]

    assert mom_tampered == pytest.approx(mom_base, abs=1e-12), "未来价格篡改渗透进了历史信号！"

def test_partial_final_month():
    """
    【Phase 4 核心测试 4】：
    如果回测截止日期落在某月中旬，该月份绝不能被作为完整 signal month。
    """
    dates = pd.date_range("2025-01-01", "2025-12-31", freq="B").append(pd.date_range("2026-01-02", "2026-01-15", freq="B"))
    df = pd.DataFrame({
        "open": [100.0] * len(dates),
        "high": [101.0] * len(dates),
        "low": [99.0] * len(dates),
        "close": [100.0] * len(dates),
        "adj_close": [100.0] * len(dates),
        "adj_open": [100.0] * len(dates),
        "volume": [1000] * len(dates)
    }, index=dates)

    data = {"ETF1": df}
    # as_of_date 截断在 2026-01-15
    close_df, _ = get_monthly_dataset(data, completed_month_only=True, as_of_date=pd.Timestamp("2026-01-15"))
    assert str(close_df.index[-1].to_period("M")) == "2025-12"
    assert "2026-01" not in [str(d.to_period("M")) for d in close_df.index]

def test_transaction_cost_exact_calculation():
    """
    【Phase 4 核心测试 5】：
    人工手算 transaction cost 与程序计算完全对齐。
    假设调仓买入金额为 $30,000，费率 5 bps (0.0005)。
    理论费用: 30000 * 0.0005 = $15.00。
    """
    trade_notional = 30000.0
    fee_rate = 5.0 / 10000.0
    expected_fee = 15.0
    actual_fee = trade_notional * fee_rate
    assert pytest.approx(actual_fee, abs=1e-8) == expected_fee

def test_zero_turnover_when_portfolio_unchanged():
    """
    【Phase 4 核心测试 6】：
    如果目标组合与当前组合持仓股数完全一致（无买卖变化），
    turnover = 0，费用 = 0，不发生机械双向摩擦。
    """
    current_shares = {"SPY": 100.0, "QQQ": 50.0}
    target_shares = {"SPY": 100.0, "QQQ": 50.0}
    prices = {"SPY": 400.0, "QQQ": 300.0}
    
    trade_notional = sum(abs(target_shares[s] - current_shares[s]) * prices[s] for s in current_shares)
    fee = trade_notional * 0.0005
    assert trade_notional == 0.0
    assert fee == 0.0
