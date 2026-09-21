import sys
import os
import argparse
from pathlib import Path
import pandas as pd

# 加入项目根目录到 sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# 设置 matplotlib 缓存目录在 workspace 内部
os.environ["MPLCONFIGDIR"] = str(PROJECT_ROOT / ".matplotlib")

from config.config import UNIVERSE, PROJECT_ROOT
from src.data.fetcher import fetch_universe_data, get_monthly_dataset
from src.backtest.engine import run_backtest, BacktestResult
from src.backtest.plotter import save_backtest_results
from src.utils.logger import logger

def print_performance_summary(name: str, res: BacktestResult) -> None:
    """以格式化表格形式输出回测成果 (含 Daily 与 Monthly 对比)"""
    m = res.metrics
    bm = res.benchmark_metrics

    print(f"\n{'='*75}")
    print(f"       回测绩效评估报告 ({name})")
    print(f"{'='*75}")
    print(f"回测时间区间: {m['Start Date']} 至 {m['End Date']} (共 {m['Years']} 年)")
    print(f"调仓执行次数: {m['Number of Rebalances']} 次")
    print(f"总交易手续费: ${m['Total Transaction Costs']:,.2f}")
    print(f"年化单边换手率 (Annualized One-Way Turnover): {m['Annualized One-Way Turnover']:.2%}")
    print(f"年化总交易额倍数 (Annualized Gross Traded Notional): {m['Annualized Gross Traded Notional']:.2%}")
    print(f"{'-'*75}")
    print(f"{'绩效指标':<32} | {'ETF Momentum Strategy':<20} | {'SPY Benchmark'}")
    print(f"{'-'*75}")
    print(f"{'初始资金 (Initial Capital)':<29} | ${m['Initial Capital']:>19,.2f} | ${bm['Initial Capital']:>15,.2f}")
    print(f"{'期末资产 (Final Wealth)':<30} | ${m['Final Wealth']:>19,.2f} | ${bm['Final Wealth']:>15,.2f}")
    print(f"{'年化收益率 (CAGR)':<32} | {m['CAGR']:>19.2%} | {bm['CAGR']:>15.2%}")
    print(f"{'-'*75}")
    print(f"{'[Daily Mark-to-Market 风险测量]':<32} | {'':<20} | {''}")
    print(f"{'  - Daily 年化波动率':<31} | {m['daily_volatility']:>19.2%} | {bm['daily_volatility']:>15.2%}")
    print(f"{'  - Daily 夏普比率 (rf=0)':<28} | {m['daily_sharpe']:>19.2f} | {bm['daily_sharpe']:>15.2f}")
    print(f"{'  - Daily CAGR / Vol':<29} | {m['daily_cagr_to_vol']:>19.2f} | {bm['daily_cagr_to_vol']:>15.2f}")
    print(f"{'  - Daily 最大回撤 (MaxDD)':<28} | {m['daily_max_drawdown']:>19.2%} | {bm['daily_max_drawdown']:>15.2%}")
    print(f"{'    * 峰值日':<33} | {m['daily_max_dd_peak_date']:>20} | {bm['daily_max_dd_peak_date']:>16}")
    print(f"{'    * 谷底日':<33} | {m['daily_max_dd_trough_date']:>20} | {bm['daily_max_dd_trough_date']:>16}")
    print(f"{'    * 恢复日':<33} | {m['daily_max_dd_recovery_date']:>20} | {bm['daily_max_dd_recovery_date']:>16}")
    print(f"{'-'*75}")
    print(f"{'[Monthly Sampled 风险测量 (平滑参考)]':<30} | {'':<20} | {''}")
    print(f"{'  - Monthly 采样年化波动率':<28} | {m['monthly_sampled_volatility']:>19.2%} | {bm['monthly_sampled_volatility']:>15.2%}")
    print(f"{'  - Monthly 采样夏普比率':<28} | {m['monthly_sampled_sharpe']:>19.2f} | {bm['monthly_sampled_sharpe']:>15.2f}")
    print(f"{'  - Monthly CAGR / Vol':<27} | {m['monthly_cagr_to_vol']:>19.2f} | {bm['monthly_cagr_to_vol']:>15.2f}")
    print(f"{'  - Monthly 采样最大回撤':<28} | {m['monthly_sampled_max_drawdown']:>19.2%} | {bm['monthly_sampled_max_drawdown']:>15.2%}")
    print(f"{'    * 峰值日':<33} | {m['monthly_max_dd_peak_date']:>20} | {bm['monthly_max_dd_peak_date']:>16}")
    print(f"{'    * 谷底日':<33} | {m['monthly_max_dd_trough_date']:>20} | {bm['monthly_max_dd_trough_date']:>16}")
    print(f"{'    * 恢复日':<33} | {m['monthly_max_dd_recovery_date']:>20} | {bm['monthly_max_dd_recovery_date']:>16}")
    print(f"{'-'*75}")

    # 年度收益排名
    strat_annual = res.annual_returns["Strategy"].dropna()
    if len(strat_annual) >= 5:
        best_5 = strat_annual.sort_values(ascending=False).head(5)
        worst_5 = strat_annual.sort_values(ascending=True).head(5)
        
        print("\n[表现最佳的 5 个自然年度]:")
        for yr, val in best_5.items():
            spy_val = res.annual_returns.loc[yr, "SPY Benchmark"]
            print(f"  {yr}年: 策略 {val:>7.2%} (SPY: {spy_val:>7.2%})")

        print("\n[表现最差的 5 个自然年度]:")
        for yr, val in worst_5.items():
            spy_val = res.annual_returns.loc[yr, "SPY Benchmark"]
            print(f"  {yr}年: 策略 {val:>7.2%} (SPY: {spy_val:>7.2%})")
    print(f"{'='*75}\n")

def run_cost_sensitivity_analysis(universe_data, start_date=None, end_date=None) -> pd.DataFrame:
    """运行费率敏感度测试 (0 / 5 / 10 / 20 bps)"""
    costs = [0.0, 5.0, 10.0, 20.0]
    records = []
    logger.info(">>> 运行交易成本与滑点敏感度分析 (Cost Sensitivity Test) <<<")
    for bps in costs:
        res = run_backtest(
            aligned_dict=universe_data,
            tx_cost_bps=bps,
            start_date=start_date,
            end_date=end_date,
            verbose=False
        )
        m = res.metrics
        records.append({
            "Cost (bps)": f"{bps:.1f} bps",
            "CAGR": f"{m['CAGR']:.2%}",
            "Final Wealth": f"${m['Final Wealth']:,.2f}",
            "Daily Sharpe": f"{m['daily_sharpe']:.2f}",
            "Monthly Sharpe": f"{m['monthly_sampled_sharpe']:.2f}",
            "Total Tx Costs": f"${m['Total Transaction Costs']:,.2f}"
        })
    df_sens = pd.DataFrame(records)
    print("\n" + "="*75)
    print("             交易成本与滑点敏感度分析 (Cost Sensitivity)")
    print("="*75)
    print(df_sens.to_string(index=False))
    print("="*75 + "\n")
    return df_sens

def main():
    parser = argparse.ArgumentParser(description="ETF Momentum 策略历史回测入口")
    parser.add_argument("--start-date", type=str, default=None, help="自定义回测起始日期 (YYYY-MM-DD)")
    parser.add_argument("--end-date", type=str, default=None, help="回测截止日期 (YYYY-MM-DD)")
    parser.add_argument("--output-dir", type=str, default="results", help="结果输出目录")
    args = parser.parse_args()

    # 1. 载入并验证对齐资产池数据
    logger.info("加载并验证本地资产池数据...")
    universe_data = fetch_universe_data(UNIVERSE)

    # 2. 运行最长历史全周期回测 (Full History)
    results_dir = PROJECT_ROOT / args.output_dir
    logger.info(">>> 运行全历史回测 (Full History) <<<")
    full_res = run_backtest(
        aligned_dict=universe_data,
        start_date=args.start_date,
        end_date=args.end_date
    )
    save_backtest_results(full_res, results_dir)
    print_performance_summary("最长可用历史", full_res)

    # 3. 运行 0/5/10/20 bps 成本敏感度测试
    df_sens = run_cost_sensitivity_analysis(universe_data, start_date=args.start_date, end_date=args.end_date)
    df_sens.to_csv(results_dir / "cost_sensitivity.csv", index=False)

    # 4. 运行 2015 至今的补充对比回测 (2015-01-01 Onwards)
    logger.info(">>> 运行 2015 至今补充对比回测 (2015-01-01 Onwards) <<<")
    sub_dir = results_dir / "2015_onwards"
    res_2015 = run_backtest(
        aligned_dict=universe_data,
        start_date="2015-01-01",
        end_date=args.end_date
    )
    save_backtest_results(res_2015, sub_dir)
    print_performance_summary("2015 至今补充区间", res_2015)

if __name__ == "__main__":
    main()
