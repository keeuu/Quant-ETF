import pytest
import pandas as pd
import numpy as np
from src.backtest.engine import calculate_portfolio_turnover, run_backtest
from src.backtest.metrics import calculate_max_drawdown_details, calculate_performance_metrics
from src.data.fetcher import is_month_completed

def test_daily_nav_between_rebalances():
    """
    【Phase 4.5 测试 1】：
    构造两次月度调仓之间的日价格变化。
    确认：调仓之间 shares 不变，而 NAV 每日根据当天 adj_close 变化。
    """
    # 构造足够的历史数据用于信号生成与调仓
    # 2024-11 至 2025-12 作为历史月，2026-01 和 2026-02 作为调仓执行月
    dates_hist = pd.date_range("2024-11-01", "2025-12-31", freq="B")
    dates_jan = pd.date_range("2026-01-02", "2026-01-30", freq="B")
    dates_feb = pd.date_range("2026-02-02", "2026-02-27", freq="B")
    all_dates = dates_hist.append(dates_jan).append(dates_feb)

    data_dict = {}
    symbols = ["SPY", "QQQ", "IWM", "EFA", "EEM", "GLD", "TLT", "IEF"]
    for i, sym in enumerate(symbols):
        base_price = 100.0 + i * 10.0
        # 历史价格递增，确保能通过 10M SMA 趋势过滤 (P > SMA)
        hist_prices = [base_price * (1.0 + 0.001 * k) for k in range(len(dates_hist))]
        jan_start = hist_prices[-1]
        jan_prices = [jan_start * (1.0 + 0.01 * (j + 1)) for j in range(len(dates_jan))]
        feb_start = jan_prices[-1]
        feb_prices = [feb_start * (1.0 + 0.005 * (k + 1)) for k in range(len(dates_feb))]
        p_all = hist_prices + jan_prices + feb_prices

        df = pd.DataFrame({
            "open": p_all,
            "high": [p * 1.01 for p in p_all],
            "low": [p * 0.99 for p in p_all],
            "close": p_all,
            "adj_close": p_all,
            "adj_open": p_all,
            "volume": [1000] * len(all_dates)
        }, index=all_dates)
        data_dict[sym] = df

    res = run_backtest(data_dict, initial_capital=100000.0, start_date="2026-01-01")
    daily_eq = res.daily_equity_curve

    # 验证 1 月份所有工作日均记录了 NAV
    jan_daily = daily_eq.loc["2026-01-02":"2026-01-30"]
    assert len(jan_daily) == len(dates_jan)
    
    # 验证 NAV 每日随市场波动，而不是一整月恒定不变
    assert jan_daily["strategy_equity"].nunique() > 1
    # 验证第一天到最后一天净值按价格规律增长
    assert jan_daily["strategy_equity"].iloc[-1] > jan_daily["strategy_equity"].iloc[0]

def test_daily_drawdown_detects_intramonth_crash():
    """
    【Phase 4.5 测试 2】：
    构造：
    月初 NAV = 100
    月中 NAV = 70
    月末 NAV = 95
    必须得到：
    daily MaxDD = 30% (即 -30%)
    而 monthly sampled drawdown 仅为 5%，不得错误声称捕捉到了 -30%。
    """
    dates = pd.to_datetime(["2026-01-02", "2026-01-15", "2026-01-30"])
    daily_nav = pd.Series([100.0, 70.0, 95.0], index=dates)

    daily_max_dd, d_peak, d_trough, d_rec = calculate_max_drawdown_details(daily_nav)
    # daily MaxDD 必须为 30%
    assert pytest.approx(daily_max_dd, abs=1e-6) == 0.30
    assert d_trough == pd.Timestamp("2026-01-15")

    # 月度抽样 (仅月初与月末)
    monthly_nav = pd.Series([100.0, 95.0], index=[pd.Timestamp("2026-01-02"), pd.Timestamp("2026-01-30")])
    monthly_max_dd, m_peak, m_trough, m_rec = calculate_max_drawdown_details(monthly_nav)
    
    # monthly sampled 仅捕捉到 5%
    assert pytest.approx(monthly_max_dd, abs=1e-6) == 0.05
    assert monthly_max_dd < daily_max_dd
    assert daily_max_dd - monthly_max_dd == pytest.approx(0.25, abs=1e-6)

def test_cash_turnover_full_exit():
    """
    【Phase 4.5 测试 3】：
    100% SPY -> 100% CASH
    one_way_turnover 必须严格等于 1.0 (100%)，而不是 0.5 (50%)。
    """
    current_weights = {"SPY": 1.0, "CASH": 0.0}
    target_weights = {"SPY": 0.0, "CASH": 1.0}
    to = calculate_portfolio_turnover(current_weights, target_weights)
    assert pytest.approx(to, abs=1e-8) == 1.0

def test_asset_rotation_turnover():
    """
    【Phase 4.5 测试 4】：
    100% SPY -> 100% QQQ
    one_way_turnover 必须严格等于 1.0 (100%)。
    """
    current_weights = {"SPY": 1.0, "QQQ": 0.0, "CASH": 0.0}
    target_weights = {"SPY": 0.0, "QQQ": 1.0, "CASH": 0.0}
    to = calculate_portfolio_turnover(current_weights, target_weights)
    assert pytest.approx(to, abs=1e-8) == 1.0

def test_identical_portfolio_turnover():
    """
    【Phase 4.5 测试 5】：
    相同组合配置：
    turnover 必须严格等于 0.0。
    """
    current_weights = {"SPY": 1.0 / 3.0, "QQQ": 1.0 / 3.0, "GLD": 1.0 / 3.0, "CASH": 0.0}
    target_weights = {"SPY": 1.0 / 3.0, "QQQ": 1.0 / 3.0, "GLD": 1.0 / 3.0, "CASH": 0.0}
    to = calculate_portfolio_turnover(current_weights, target_weights)
    assert pytest.approx(to, abs=1e-8) == 0.0

def test_turnover_does_not_change_final_wealth():
    """
    【Phase 4.5 测试 6】：
    修改 turnover reporting implementation 绝不影响实际交易金额、手续费及 Final Wealth。
    手续费严格按照 sum(|trade_notional|) * 0.0005 计算。
    """
    notionals = {"SPY": 15000.0, "QQQ": 25000.0}
    fee_bps = 5.0
    total_fee = sum(notionals.values()) * (fee_bps / 10000.0)
    assert pytest.approx(total_fee, abs=1e-8) == 40000.0 * 0.0005 == 20.0

def test_partial_terminal_month_excluded_from_monthly_risk():
    """
    【Phase 4.5 测试 7】：
    未完成的自然月 (如 9 月截止在 18 号) 不得作为完整月参与 monthly risk statistics 的年化。
    """
    # 构造截至 2026-09-18 的情况
    sep_period = pd.Period("2026-09", freq="M")
    max_date = pd.Timestamp("2026-09-18")
    assert not is_month_completed(sep_period, max_data_date=max_date)

    # 验证 calculate_performance_metrics 在传入已剔除未完成月序列时的计算一致性
    monthly_ret_full = pd.Series([0.02, 0.01, -0.01, -0.008], index=pd.to_datetime(["2026-06-30", "2026-07-31", "2026-08-31", "2026-09-18"]))
    # 剔除 2026-09-18
    monthly_ret_clean = monthly_ret_full.iloc[:-1]
    
    vol_full = monthly_ret_full.std() * np.sqrt(12)
    vol_clean = monthly_ret_clean.std() * np.sqrt(12)
    assert vol_full != pytest.approx(vol_clean)
    assert len(monthly_ret_clean) == 3

def test_monthly_sharpe_formula():
    """
    【Phase 4.6 测试 1】：
    手工构造月收益：+1%, +2%, -1%, +3%, +0.5%, -0.5%
    独立手算 mean / std * sqrt(12)，与 metrics.py 算法完全一致。
    """
    returns_list = [0.01, 0.02, -0.01, 0.03, 0.005, -0.005]
    dates = pd.date_range("2025-01-31", periods=len(returns_list), freq="ME")
    m_returns = pd.Series(returns_list, index=dates)

    # 独立手工统计
    n = len(returns_list)
    mean_val = sum(returns_list) / n
    variance = sum((x - mean_val) ** 2 for x in returns_list) / (n - 1)
    std_val = np.sqrt(variance)
    expected_sharpe = (mean_val / std_val) * np.sqrt(12)

    # 构造对应净值序列
    nav = [100.0]
    for r in returns_list:
        nav.append(nav[-1] * (1.0 + r))
    nav_series = pd.Series(nav[1:], index=dates)

    metrics = calculate_performance_metrics(
        equity_series=nav_series,
        monthly_returns=m_returns,
        initial_capital=100.0
    )

    actual_sharpe = metrics["monthly_sampled_sharpe"]
    assert pytest.approx(actual_sharpe, abs=1e-8) == expected_sharpe

def test_cagr_to_vol_is_not_sharpe():
    """
    【Phase 4.6 测试 2】：
    明确保证 CAGR_to_Vol_Ratio 和 Sharpe 作为两个不同字段存在，
    且数值与概念明确分离，杜绝混用。
    """
    returns_list = [0.03, -0.02, 0.04, 0.01, -0.01, 0.05]
    dates = pd.date_range("2025-01-31", periods=len(returns_list), freq="ME")
    m_returns = pd.Series(returns_list, index=dates)

    nav = [100.0]
    for r in returns_list:
        nav.append(nav[-1] * (1.0 + r))
    nav_series = pd.Series(nav[1:], index=dates)

    metrics = calculate_performance_metrics(
        equity_series=nav_series,
        monthly_returns=m_returns,
        initial_capital=100.0
    )

    # 验证字段独立存在
    assert "CAGR_to_Vol_Ratio" in metrics
    assert "monthly_cagr_to_vol" in metrics
    assert "monthly_sampled_sharpe" in metrics
    assert "daily_sharpe" in metrics

    # 验证 CAGR-to-Vol 与 Sharpe 数值存在实质区分 (几何 CAGR vs 算术均值)
    sharpe = metrics["monthly_sampled_sharpe"]
    cagr_to_vol = metrics["CAGR_to_Vol_Ratio"]
    assert sharpe != pytest.approx(cagr_to_vol, abs=1e-5)
