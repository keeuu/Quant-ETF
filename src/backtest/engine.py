import pandas as pd
import numpy as np
from typing import Dict, List, Tuple, Optional, Any
from config.config import UNIVERSE, BENCHMARK_SYMBOL, TRANSACTION_COST_BPS, TOP_N, CASH_RETURN
from src.data.fetcher import (
    get_monthly_dataset, 
    build_rebalance_schedule, 
    RebalanceEvent,
    is_month_completed
)
from src.signals.momentum import calculate_12_1_momentum
from src.signals.trend import check_trend_filter
from src.signals.selector import select_top_assets
from src.backtest.metrics import calculate_performance_metrics, calculate_calendar_annual_returns
from src.utils.logger import logger

def calculate_portfolio_turnover(
    current_weights: Dict[str, float],
    target_weights: Dict[str, float]
) -> float:
    """
    计算包含 CASH 的投资组合单边换手率 (One-Way Portfolio Turnover)。
    公式: 0.5 * sum(|target_weight_i - current_weight_i|) 涵盖所有标的以及 CASH。
    """
    all_keys = set(current_weights.keys()).union(set(target_weights.keys()))
    diff_sum = sum(abs(target_weights.get(k, 0.0) - current_weights.get(k, 0.0)) for k in all_keys)
    return 0.5 * diff_sum

class BacktestResult:
    """回测结果包装类"""
    def __init__(
        self,
        metrics: Dict[str, Any],
        equity_curve: pd.DataFrame,
        monthly_returns: pd.DataFrame,
        rebalance_log: pd.DataFrame,
        annual_returns: pd.DataFrame,
        benchmark_metrics: Dict[str, Any],
        daily_equity_curve: Optional[pd.DataFrame] = None,
        daily_returns: Optional[pd.DataFrame] = None
    ):
        self.metrics = metrics
        self.equity_curve = equity_curve
        self.monthly_returns = monthly_returns
        self.rebalance_log = rebalance_log
        self.annual_returns = annual_returns
        self.benchmark_metrics = benchmark_metrics
        self.daily_equity_curve = daily_equity_curve if daily_equity_curve is not None else pd.DataFrame()
        self.daily_returns = daily_returns if daily_returns is not None else pd.DataFrame()


def run_backtest(
    aligned_dict: Dict[str, pd.DataFrame],
    initial_capital: float = 100000.0,
    tx_cost_bps: float = TRANSACTION_COST_BPS,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    top_n: int = TOP_N,
    benchmark_symbol: str = BENCHMARK_SYMBOL,
    verbose: bool = False
) -> BacktestResult:
    """
    运行严格无未来函数的 ETF 动量回测引擎。
    
    【核心保证】：
    1. 调仓信号仅在 signal_date (月末收盘后) 计算；
    2. 成交执行严格在 execution_date (次月初开盘) 以 adj_open 代理价格完成；
    3. 严格收取 5 bps 交易成本，并支持零换手检测；
    4. 现金收益率固定为 0%。
    """
    logger.info(f"=== 开始执行历史回测 (初始资金: ${initial_capital:,.2f}, 费率: {tx_cost_bps} bps) ===")

    as_of = pd.Timestamp(end_date) if end_date else None
    
    # 1. 提取完整月份数据集与显式调仓时间线日程表
    monthly_close, monthly_open = get_monthly_dataset(aligned_dict, completed_month_only=True, as_of_date=as_of)
    full_schedule = build_rebalance_schedule(aligned_dict, as_of_date=as_of)

    # 2. 向量化计算全部历史信号矩阵
    mom_df = calculate_12_1_momentum(monthly_close)
    trend_df = check_trend_filter(monthly_close)

    # 3. 确定首个合法调仓周期 (动量与均线均有非 NaN 值的最早周期)
    valid_schedule: List[RebalanceEvent] = []
    for evt in full_schedule:
        if evt.signal_date not in mom_df.index:
            continue
        row_mom = mom_df.loc[evt.signal_date]
        # 要求至少有有效动量值计算出来 (非全部 NaN)
        if row_mom.dropna().empty:
            continue
        
        # 若指定了自定义 start_date，则在此日期之后的首个调仓日开始
        if start_date is not None:
            if evt.execution_date < pd.Timestamp(start_date):
                continue

        valid_schedule.append(evt)

    if not valid_schedule:
        raise ValueError("在指定的时间范围内没有找到具备充分历史数据的合法调仓周期！")

    first_event = valid_schedule[0]
    last_event = valid_schedule[-1]
    logger.info(f"首个合法信号日: {first_event.signal_date.date()}, 首个调仓执行日: {first_event.execution_date.date()}")
    logger.info(f"回测覆盖区间: {first_event.execution_date.date()} -> {last_event.execution_date.date()} (共 {len(valid_schedule)} 个调仓周期)")

    # 4. 初始化账户状态
    cash = float(initial_capital)
    positions: Dict[str, float] = {sym: 0.0 for sym in UNIVERSE} # 各资产持有股数
    
    # 初始化 SPY 基准账户
    bench_cash = 0.0
    first_bench_open = aligned_dict[benchmark_symbol].loc[first_event.execution_date, "adj_open"]
    # 扣除首笔建仓费率
    bench_initial_fee = initial_capital * (tx_cost_bps / 10000.0)
    bench_shares = (initial_capital - bench_initial_fee) / first_bench_open

    # 提取回测有效日历交易日全集
    sample_sym = next(iter(aligned_dict.keys()))
    all_trading_days = aligned_dict[sample_sym].index
    end_cutoff = pd.Timestamp(end_date) if end_date else all_trading_days[-1]
    trading_dates_subset = all_trading_days[(all_trading_days >= first_event.execution_date) & (all_trading_days <= end_cutoff)]

    # 记录轨迹
    rebalance_records = []
    daily_equity_records = []
    equity_curve_records = [
        {
            "date": first_event.execution_date,
            "strategy_equity": float(initial_capital),
            "benchmark_equity": float(initial_capital)
        }
    ]

    strategy_accum_tx_costs = 0.0
    cycle_one_way_turnover_rates: List[float] = []
    cycle_gross_trade_ratios: List[float] = []

    # 5. 逐个调仓周期推进
    for idx, evt in enumerate(valid_schedule):
        sig_date = evt.signal_date
        exec_date = evt.execution_date
        period_str = evt.period

        if idx < len(valid_schedule) - 1:
            next_exec_date = valid_schedule[idx + 1].execution_date
            cycle_trading_dates = trading_dates_subset[(trading_dates_subset >= exec_date) & (trading_dates_subset < next_exec_date)]
            period_end_date = valid_schedule[idx + 1].signal_date
        else:
            cycle_trading_dates = trading_dates_subset[trading_dates_subset >= exec_date]
            period_end_date = trading_dates_subset[-1]

        # 当期执行价格向量 (次月初 Open)
        exec_prices = {sym: aligned_dict[sym].loc[exec_date, "adj_open"] for sym in UNIVERSE}

        # A. 计算开盘调仓前资产净值
        portfolio_val_pre = cash + sum(positions[sym] * exec_prices[sym] for sym in UNIVERSE)

        # B. 计算当前投资组合权重 (严格将 CASH 纳入资产全集)
        current_weights = {
            sym: (positions[sym] * exec_prices[sym]) / portfolio_val_pre if portfolio_val_pre > 0 else 0.0
            for sym in UNIVERSE
        }
        current_weights["CASH"] = cash / portfolio_val_pre if portfolio_val_pre > 0 else 0.0

        # C. 获取上月末生成的策略目标权重
        curr_mom = mom_df.loc[sig_date]
        curr_trend = trend_df.loc[sig_date]
        target_weights = select_top_assets(curr_mom, curr_trend, top_n=top_n, verbose=False)

        target_weights_full = {sym: target_weights.get(sym, 0.0) for sym in UNIVERSE}
        target_weights_full["CASH"] = max(0.0, 1.0 - sum(target_weights_full.values()))

        # D. 计算 One-Way Portfolio Turnover (涵盖 CASH 资产)
        cycle_one_way_to = calculate_portfolio_turnover(current_weights, target_weights_full)
        cycle_one_way_turnover_rates.append(cycle_one_way_to)

        # E. 计算预留手续费后的可分配资本
        allocatable_equity = portfolio_val_pre * (1.0 - (tx_cost_bps / 10000.0))

        # F. 计算目标股数与调仓交易额
        target_shares = {}
        trade_notionals = {}
        total_cycle_tx_cost = 0.0
        gross_traded_notional = 0.0

        for sym in UNIVERSE:
            target_w = target_weights.get(sym, 0.0)
            t_val = allocatable_equity * target_w
            t_shares = t_val / exec_prices[sym] if exec_prices[sym] > 0 else 0.0
            target_shares[sym] = t_shares

            delta_s = t_shares - positions[sym]
            notional = abs(delta_s) * exec_prices[sym]
            trade_notionals[sym] = notional
            
            # 手续费 (固定费率)
            fee = notional * (tx_cost_bps / 10000.0)
            total_cycle_tx_cost += fee
            gross_traded_notional += notional

        gross_trade_ratio = gross_traded_notional / portfolio_val_pre if portfolio_val_pre > 0 else 0.0
        cycle_gross_trade_ratios.append(gross_trade_ratio)

        # G. 执行调仓与会计更新
        cash = portfolio_val_pre - sum(target_shares[s] * exec_prices[s] for s in UNIVERSE) - total_cycle_tx_cost
        if abs(cash) < 1e-4:
            cash = 0.0
        elif cash < 0:
            logger.debug(f"[{exec_date.date()}] 微调现金缓冲: {cash}")
            cash = 0.0

        positions = target_shares.copy()
        portfolio_val_post = cash + sum(positions[s] * exec_prices[s] for s in UNIVERSE)
        strategy_accum_tx_costs += total_cycle_tx_cost

        # H. 记录调仓日志
        selected_symbols = [s for s, w in target_weights.items() if w > 0]
        rebalance_records.append({
            "period": period_str,
            "signal_date": sig_date.strftime("%Y-%m-%d"),
            "execution_date": exec_date.strftime("%Y-%m-%d"),
            "selected_assets": ", ".join(selected_symbols) if selected_symbols else "CASH",
            "portfolio_val_pre": portfolio_val_pre,
            "portfolio_val_post": portfolio_val_post,
            "cycle_turnover": gross_traded_notional,
            "cycle_one_way_turnover": cycle_one_way_to,
            "cycle_gross_trade_ratio": gross_trade_ratio,
            "cycle_tx_cost": total_cycle_tx_cost,
            "cash_post": cash
        })

        # I. Daily Mark-to-Market (持仓在两个调仓日之间保持恒定股数)
        for d in cycle_trading_dates:
            d_pos_val = sum(positions[s] * aligned_dict[s].loc[d, "adj_close"] for s in UNIVERSE)
            d_strat_equity = cash + d_pos_val
            d_bench_equity = bench_cash + bench_shares * aligned_dict[benchmark_symbol].loc[d, "adj_close"]
            d_gross_exp = d_pos_val / d_strat_equity if d_strat_equity > 0 else 0.0
            daily_equity_records.append({
                "date": d,
                "strategy_equity": d_strat_equity,
                "benchmark_equity": d_bench_equity,
                "cash": cash,
                "position_value": d_pos_val,
                "gross_exposure": d_gross_exp
            })

        # J. 计算持仓至当月末最后交易日或最新可用交易日的月度抽样净值
        strat_end_val = cash + sum(
            positions[s] * aligned_dict[s].loc[period_end_date, "adj_close"] for s in UNIVERSE
        )
        bench_end_val = bench_shares * aligned_dict[benchmark_symbol].loc[period_end_date, "adj_close"]

        equity_curve_records.append({
            "date": period_end_date,
            "strategy_equity": strat_end_val,
            "benchmark_equity": bench_end_val
        })

    # 6. 构造绩效时间序列 (Daily & Monthly)
    df_daily = pd.DataFrame(daily_equity_records).set_index("date")
    df_equity = pd.DataFrame(equity_curve_records).set_index("date")
    df_rebal = pd.DataFrame(rebalance_records).set_index("execution_date")

    # 计算 Daily 收益率序列
    df_daily["strategy_return"] = df_daily["strategy_equity"].pct_change().dropna()
    df_daily["benchmark_return"] = df_daily["benchmark_equity"].pct_change().dropna()
    daily_returns = df_daily[["strategy_return", "benchmark_return"]].dropna().copy()

    # 计算 Monthly 收益率序列 (各月末净值相对于前一个记录点的变化)
    df_equity["strategy_return"] = df_equity["strategy_equity"].pct_change().dropna()
    df_equity["benchmark_return"] = df_equity["benchmark_equity"].pct_change().dropna()
    monthly_returns = df_equity[["strategy_return", "benchmark_return"]].dropna().copy()

    # 严格排除未完成的终点自然月用于月度风险指标年化 (Terminal Partial Month Exclusion)
    last_m = df_equity.index[-1].to_period("M")
    max_dt = df_daily.index[-1]
    if not is_month_completed(last_m, max_data_date=max_dt, as_of_date=as_of):
        monthly_returns_for_risk = df_equity.iloc[:-1][["strategy_return", "benchmark_return"]].dropna().copy()
    else:
        monthly_returns_for_risk = monthly_returns.copy()

    # 7. 计算指标
    strat_metrics = calculate_performance_metrics(
        equity_series=df_daily["strategy_equity"],
        daily_returns=daily_returns["strategy_return"],
        monthly_equity_series=df_equity["strategy_equity"],
        monthly_returns=monthly_returns_for_risk["strategy_return"],
        initial_capital=initial_capital,
        rf=CASH_RETURN,
        rebalance_count=len(valid_schedule),
        total_tx_costs=strategy_accum_tx_costs,
        cumulative_one_way_turnover=sum(cycle_one_way_turnover_rates),
        cumulative_gross_trade_ratio=sum(cycle_gross_trade_ratios)
    )

    bench_metrics = calculate_performance_metrics(
        equity_series=df_daily["benchmark_equity"],
        daily_returns=daily_returns["benchmark_return"],
        monthly_equity_series=df_equity["benchmark_equity"],
        monthly_returns=monthly_returns_for_risk["benchmark_return"],
        initial_capital=initial_capital,
        rf=CASH_RETURN,
        rebalance_count=1,
        total_tx_costs=bench_initial_fee,
        cumulative_one_way_turnover=1.0,
        cumulative_gross_trade_ratio=1.0
    )

    # 8. 计算各自然年度收益
    strat_annual = calculate_calendar_annual_returns(monthly_returns["strategy_return"])
    bench_annual = calculate_calendar_annual_returns(monthly_returns["benchmark_return"])
    annual_df = pd.DataFrame({
        "Strategy": strat_annual,
        "SPY Benchmark": bench_annual
    })

    return BacktestResult(
        metrics=strat_metrics,
        equity_curve=df_equity,
        monthly_returns=monthly_returns,
        rebalance_log=df_rebal,
        annual_returns=annual_df,
        benchmark_metrics=bench_metrics,
        daily_equity_curve=df_daily,
        daily_returns=daily_returns
    )
