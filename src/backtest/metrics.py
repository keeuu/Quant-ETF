import pandas as pd
import numpy as np
from typing import Dict, Any, Tuple, Optional

def calculate_cagr(start_val: float, end_val: float, years: float) -> float:
    """计算年化复合增长率 (CAGR)"""
    if start_val <= 0 or end_val <= 0 or years <= 0:
        return 0.0
    return (end_val / start_val) ** (1.0 / years) - 1.0

def calculate_max_drawdown_details(equity_series: pd.Series) -> Tuple[float, Optional[pd.Timestamp], Optional[pd.Timestamp], Optional[pd.Timestamp]]:
    """
    计算最大回撤及其关键时间节点：
    返回: (max_drawdown, peak_date, trough_date, recovery_date)
    """
    if len(equity_series) < 2:
        return 0.0, None, None, None

    running_max = equity_series.cummax()
    drawdown = (equity_series - running_max) / running_max
    max_dd = abs(drawdown.min())

    trough_date = drawdown.idxmin()
    # 谷底之前的最高峰
    peak_date = equity_series.loc[:trough_date].idxmax()
    peak_val = equity_series.loc[peak_date]

    # 谷底之后的恢复日期 (净值重新超过或等于峰值)
    after_trough = equity_series.loc[trough_date:]
    recovered = after_trough[after_trough >= peak_val]
    recovery_date = recovered.index[0] if len(recovered) > 0 else None

    return float(max_dd), peak_date, trough_date, recovery_date

def calculate_performance_metrics(
    equity_series: pd.Series,
    monthly_returns: Optional[pd.Series] = None,
    daily_returns: Optional[pd.Series] = None,
    monthly_equity_series: Optional[pd.Series] = None,
    initial_capital: Optional[float] = None,
    rf: float = 0.0,
    rebalance_count: int = 0,
    total_tx_costs: float = 0.0,
    cumulative_turnover_rates: float = 0.0,
    cumulative_one_way_turnover: Optional[float] = None,
    cumulative_gross_trade_ratio: float = 0.0
) -> Dict[str, Any]:
    """
    全面计算策略绩效指标矩阵 (包含 Daily Mark-to-Market 与 Monthly Sampled 风险指标)。
    """
    if len(equity_series) < 2:
        return {}

    start_date = equity_series.index[0]
    end_date = equity_series.index[-1]
    days = (end_date - start_date).days
    years = max(days / 365.25, 1.0 / 12.0)

    start_val = float(initial_capital) if initial_capital is not None else float(equity_series.iloc[0])
    final_wealth = float(equity_series.iloc[-1])

    cagr = calculate_cagr(start_val, final_wealth, years)

    # 1. Daily 风险指标
    if daily_returns is None or len(daily_returns) == 0:
        daily_returns = equity_series.pct_change().dropna()

    daily_vol = float(daily_returns.std() * np.sqrt(252)) if len(daily_returns) > 1 else 0.0
    daily_sharpe = float((daily_returns.mean() / daily_returns.std()) * np.sqrt(252)) if (len(daily_returns) > 1 and daily_returns.std() > 1e-6) else 0.0
    daily_max_dd, d_peak, d_trough, d_rec = calculate_max_drawdown_details(equity_series)

    # 2. Monthly Sampled 风险指标
    if monthly_returns is None:
        monthly_returns = pd.Series(dtype=float)
    if monthly_equity_series is None:
        monthly_equity_series = equity_series

    monthly_vol = float(monthly_returns.std() * np.sqrt(12)) if len(monthly_returns) > 1 else 0.0
    # 标准 arithmetic Monthly Sharpe (mean / std * sqrt(12))
    monthly_sharpe = float((monthly_returns.mean() / monthly_returns.std()) * np.sqrt(12)) if (len(monthly_returns) > 1 and monthly_returns.std() > 1e-6) else 0.0
    monthly_max_dd, m_peak, m_trough, m_rec = calculate_max_drawdown_details(monthly_equity_series)

    # 3. CAGR-to-Vol 比率 (明确独立命名，杜绝与 Sharpe 混用)
    daily_cagr_to_vol = float(cagr / daily_vol) if daily_vol > 1e-6 else 0.0
    monthly_cagr_to_vol = float(cagr / monthly_vol) if monthly_vol > 1e-6 else 0.0

    # 4. 换手率指标
    eff_one_way_turnover = cumulative_one_way_turnover if cumulative_one_way_turnover is not None else cumulative_turnover_rates
    ann_one_way_turnover = eff_one_way_turnover / years if years > 0 else 0.0
    ann_gross_traded = cumulative_gross_trade_ratio / years if years > 0 else 0.0

    metrics = {
        "Start Date": start_date.strftime("%Y-%m-%d"),
        "End Date": end_date.strftime("%Y-%m-%d"),
        "Years": round(years, 2),
        "Initial Capital": round(start_val, 2),
        "Final Wealth": round(final_wealth, 2),
        "CAGR": cagr,
        
        # 显式 Daily 风险指标
        "daily_volatility": daily_vol,
        "daily_sharpe": daily_sharpe,
        "daily_max_drawdown": daily_max_dd,
        "daily_max_dd_peak_date": d_peak.strftime("%Y-%m-%d") if d_peak is not None else "N/A",
        "daily_max_dd_trough_date": d_trough.strftime("%Y-%m-%d") if d_trough is not None else "N/A",
        "daily_max_dd_recovery_date": d_rec.strftime("%Y-%m-%d") if d_rec is not None else "Not Recovered",

        # 显式 Monthly Sampled 风险指标
        "monthly_sampled_volatility": monthly_vol,
        "monthly_sampled_sharpe": monthly_sharpe,
        "monthly_sampled_max_drawdown": monthly_max_dd,
        "monthly_max_dd_peak_date": m_peak.strftime("%Y-%m-%d") if m_peak is not None else "N/A",
        "monthly_max_dd_trough_date": m_trough.strftime("%Y-%m-%d") if m_trough is not None else "N/A",
        "monthly_max_dd_recovery_date": m_rec.strftime("%Y-%m-%d") if m_rec is not None else "Not Recovered",

        # CAGR / Vol 比率 (明确独立命名)
        "daily_cagr_to_vol": daily_cagr_to_vol,
        "monthly_cagr_to_vol": monthly_cagr_to_vol,
        "CAGR_to_Vol_Ratio": monthly_cagr_to_vol,

        # 调仓与成本
        "Number of Rebalances": rebalance_count,
        "Total Transaction Costs": round(total_tx_costs, 2),
        "Annualized One-Way Turnover": ann_one_way_turnover,
        "Annualized Gross Traded Notional": ann_gross_traded,

        # 向后兼容字段 (Backward Compatibility)
        "Annualized Volatility": daily_vol,
        "Sharpe Ratio (rf=0)": daily_sharpe,
        "Max Drawdown": daily_max_dd,
        "Max DD Peak Date": d_peak.strftime("%Y-%m-%d") if d_peak is not None else "N/A",
        "Max DD Trough Date": d_trough.strftime("%Y-%m-%d") if d_trough is not None else "N/A",
        "Max DD Recovery Date": d_rec.strftime("%Y-%m-%d") if d_rec is not None else "Not Recovered",
        "Annualized Turnover": ann_one_way_turnover
    }

    return metrics

def calculate_calendar_annual_returns(monthly_returns: pd.Series) -> pd.Series:
    """计算各自然年度收益率"""
    if monthly_returns.empty:
        return pd.Series(dtype=float)
    # (1 + r).groupby(year).prod() - 1
    annual = (1.0 + monthly_returns).groupby(monthly_returns.index.year).prod() - 1.0
    return annual
