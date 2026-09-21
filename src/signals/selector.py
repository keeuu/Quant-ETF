import pandas as pd
import numpy as np
from typing import Dict, List, Tuple, Any
from config.config import TOP_N
from src.utils.logger import logger

def select_top_assets(
    momentum_scores: pd.Series,
    trend_passed: pd.Series,
    top_n: int = TOP_N,
    verbose: bool = True
) -> Dict[str, float]:
    """
    根据 10M SMA 趋势过滤与 12-1 动量排名选择最终持仓组合及权重。
    
    规则：
    1. 趋势过滤：仅在 trend_passed == True 的候选池中选拔；
    2. 动量排序：按 12-1 Momentum 从高到低降序排列；
    3. 等权配置：
       - 若通过数 >= 3: 选 Top 3，各 1/3
       - 若通过数 == 2: 各 50%
       - 若通过数 == 1: 占 100%
       - 若通过数 == 0: 保持 100% 现金 (所有资产权重为 0)
       
    返回:
        Dict[str, float]: 选中的 ETF 及其目标权重 (e.g. {'SPY': 0.333333, 'QQQ': 0.333333, 'GLD': 0.333334})
    """
    # 确保对齐
    common_symbols = [s for s in momentum_scores.index if s in trend_passed.index]
    mom = momentum_scores.loc[common_symbols].dropna()
    trend = trend_passed.loc[common_symbols]

    # 1. 过滤出通过趋势线的资产
    qualified_symbols = [s for s in mom.index if bool(trend.get(s, False))]
    disqualified_symbols = [s for s in mom.index if s not in qualified_symbols]

    if verbose:
        logger.info(f"--- 调仓资产选择审计 ---")
        if disqualified_symbols:
            logger.info(f"未通过 10M SMA 趋势过滤的资产 ({len(disqualified_symbols)} 只): {disqualified_symbols}")
        logger.info(f"通过 10M SMA 趋势过滤的资产 ({len(qualified_symbols)} 只): {qualified_symbols}")

    if not qualified_symbols:
        if verbose:
            logger.warning("没有资产通过 10M SMA 趋势过滤！组合本期保持 100% 现金。")
        return {}

    # 2. 按 12-1 动量从高到低排序
    sorted_mom = mom.loc[qualified_symbols].sort_values(ascending=False)
    if verbose:
        ranking_info = [f"{s}: {sorted_mom[s]:.2%}" for s in sorted_mom.index]
        logger.info(f"通过趋势过滤资产的 12-1 动量排名: {', '.join(ranking_info)}")

    # 3. 选拔与等权配置
    num_qualified = len(sorted_mom)
    num_to_select = min(num_qualified, top_n)
    selected_symbols = list(sorted_mom.index[:num_to_select])

    weights = {}
    target_weight = 1.0 / num_to_select
    for sym in selected_symbols:
        weights[sym] = target_weight

    if verbose:
        alloc_info = [f"{s}: {weights[s]:.2%}" for s in selected_symbols]
        logger.info(f"最终选定持仓资产 ({len(selected_symbols)} 只): {', '.join(alloc_info)}")

    return weights
