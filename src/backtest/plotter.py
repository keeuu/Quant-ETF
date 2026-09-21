import os
import matplotlib
matplotlib.use("Agg") # 无图形界面服务器模式
import matplotlib.pyplot as plt
import pandas as pd
from pathlib import Path
from src.backtest.engine import BacktestResult
from src.utils.logger import logger

def save_backtest_results(result: BacktestResult, output_dir: Path) -> None:
    """
    将回测产生的数据与图表持久化保存至 output_dir 目录。
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    # 1. 保存 metrics.csv
    df_metrics = pd.DataFrame([
        {"Metric": k, "Strategy": v, "SPY Benchmark": result.benchmark_metrics.get(k, "N/A")}
        for k, v in result.metrics.items()
    ])
    df_metrics.to_csv(output_dir / "metrics.csv", index=False)

    # 2. 保存 monthly_returns.csv
    result.monthly_returns.to_csv(output_dir / "monthly_returns.csv")

    # 3. 保存 equity_curve.csv (月度抽样) 与 daily_equity_curve.csv (日度市值跟踪)
    result.equity_curve.to_csv(output_dir / "equity_curve.csv")
    if hasattr(result, "daily_equity_curve") and not result.daily_equity_curve.empty:
        result.daily_equity_curve.to_csv(output_dir / "daily_equity_curve.csv")

    # 4. 保存 rebalance_log.csv
    result.rebalance_log.to_csv(output_dir / "rebalance_log.csv")

    # 5. 保存 annual_returns.csv
    result.annual_returns.to_csv(output_dir / "annual_returns.csv")

    # 6. 生成并保存 equity_curve.png (优先使用高精度的 daily_equity_curve)
    plot_curve = result.daily_equity_curve if (hasattr(result, "daily_equity_curve") and not result.daily_equity_curve.empty) else result.equity_curve
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 8), sharex=True, gridspec_kw={"height_ratios": [3, 1]})

    # 净值主图
    ax1.plot(plot_curve.index, plot_curve["strategy_equity"], label=f"ETF Momentum (CAGR: {result.metrics['CAGR']:.2%})", color="#1f77b4", lw=2)
    ax1.plot(plot_curve.index, plot_curve["benchmark_equity"], label=f"SPY Benchmark (CAGR: {result.benchmark_metrics['CAGR']:.2%})", color="#ff7f0e", lw=1.5, alpha=0.8)
    ax1.set_title("ETF Momentum Strategy vs SPY Buy & Hold (Daily Mark-to-Market Equity Curve)", fontsize=14, fontweight="bold")
    ax1.set_ylabel("Portfolio Value ($)", fontsize=12)
    ax1.legend(loc="upper left", fontsize=11)
    ax1.grid(True, linestyle="--", alpha=0.6)

    # 回撤副图
    strat_cummax = plot_curve["strategy_equity"].cummax()
    strat_dd = (plot_curve["strategy_equity"] - strat_cummax) / strat_cummax
    bench_cummax = plot_curve["benchmark_equity"].cummax()
    bench_dd = (plot_curve["benchmark_equity"] - bench_cummax) / bench_cummax

    max_dd_label = result.metrics.get("daily_max_drawdown", result.metrics.get("Max Drawdown", 0.0))
    bench_dd_label = result.benchmark_metrics.get("daily_max_drawdown", result.benchmark_metrics.get("Max Drawdown", 0.0))

    ax2.plot(plot_curve.index, strat_dd, label=f"Strategy Daily MaxDD: {max_dd_label:.2%}", color="#1f77b4", lw=1.5)
    ax2.plot(plot_curve.index, bench_dd, label=f"SPY Daily MaxDD: {bench_dd_label:.2%}", color="#ff7f0e", lw=1.2, alpha=0.7)
    ax2.set_ylabel("Drawdown", fontsize=12)
    ax2.set_xlabel("Date", fontsize=12)
    ax2.legend(loc="lower left", fontsize=10)
    ax2.grid(True, linestyle="--", alpha=0.6)

    plt.tight_layout()
    chart_path = output_dir / "equity_curve.png"
    plt.savefig(chart_path, dpi=200)
    plt.close()

    logger.info(f"回测结果文件已全部保存至目录: {output_dir}")
