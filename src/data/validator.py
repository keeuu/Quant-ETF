import pandas as pd
from typing import Dict, List, Tuple
from src.utils.logger import logger

REQUIRED_COLUMNS = ["open", "high", "low", "close", "adj_close", "volume", "adj_open"]

class DataValidationError(Exception):
    """数据校验异常基类"""
    pass

def validate_single_etf_data(df: pd.DataFrame, symbol: str) -> None:
    """
    针对单只 ETF 的历史行情数据进行严格完整性检验。
    检验项包括：必须字段、索引类型、时间递增性、重复日期、空值(NaN)、非正价格。
    """
    if df.empty:
        raise DataValidationError(f"[{symbol}] 数据集为空，无法用于回测或信号生成。")

    # 1. 检查索引是否为 DatetimeIndex
    if not isinstance(df.index, pd.DatetimeIndex):
        raise DataValidationError(f"[{symbol}] 索引必须为 pd.DatetimeIndex，当前为: {type(df.index)}")

    # 2. 检查必须列
    missing_cols = [col for col in REQUIRED_COLUMNS if col not in df.columns]
    if missing_cols:
        raise DataValidationError(f"[{symbol}] 缺失必须的数据列: {missing_cols}")

    # 3. 检查日期唯一性 (无重复日期)
    if df.index.duplicated().any():
        dups = df.index[df.index.duplicated()].tolist()
        raise DataValidationError(f"[{symbol}] 发现重复交易日期: {dups}")

    # 4. 检查日期单调递增性
    if not df.index.is_monotonic_increasing:
        raise DataValidationError(f"[{symbol}] 日期索引未按时间单调递增。")

    # 5. 检查 NaN 或空值
    nan_counts = df[REQUIRED_COLUMNS].isna().sum()
    if nan_counts.any():
        bad_cols = nan_counts[nan_counts > 0].to_dict()
        raise DataValidationError(f"[{symbol}] 数据中存在 NaN 空值，拒绝静默填充: {bad_cols}")

    # 6. 检查价格合法性 (所有价格必须 > 0)
    for price_col in ["open", "high", "low", "close", "adj_close", "adj_open"]:
        if (df[price_col] <= 0).any():
            invalid_rows = df[df[price_col] <= 0]
            raise DataValidationError(f"[{symbol}] 列 [{price_col}] 存在非法价格 (<= 0)，行数: {len(invalid_rows)}")

    # 7. 检查成交量 (Volume >= 0)
    if (df["volume"] < 0).any():
        raise DataValidationError(f"[{symbol}] volume 存在负数。")

    logger.debug(f"[{symbol}] 数据质检通过: 共 {len(df)} 行，时间跨度: {df.index[0].date()} -> {df.index[-1].date()}")

def pre_alignment_validation(raw_data_dict: Dict[str, pd.DataFrame]) -> Dict[str, dict]:
    """
    【Phase 3.5 强化】：在执行多资产公共日期对齐之前，进行全面的独立前置数据审查。
    
    目的：避免取交集 (Inner Join) 时静默吞掉某只 ETF 的数据源异常缺失。
    
    检查与报告内容：
    1. 各 ETF 的原始交易日数量、起始日期、结束日期；
    2. 单资产内部的重复日期、NaN、非法价格；
    3. 各 ETF 与资产池全集（所有交易日并集 Union）的比对，详细记录异常缺失日期和缺口。
    
    返回:
        Dict[str, dict]: 各资产的原始质检统计报告。
    """
    if not raw_data_dict:
        raise DataValidationError("原始数据字典为空，无法执行前置审查。")

    report = {}
    all_dates_union = set()

    for sym, df in raw_data_dict.items():
        validate_single_etf_data(df, sym)
        all_dates_union.update(set(df.index))
        report[sym] = {
            "count": len(df),
            "start_date": df.index[0].date(),
            "end_date": df.index[-1].date(),
            "missing_dates": []
        }

    total_calendar_trading_days = len(all_dates_union)
    logger.info(f"--- 资产池前置对齐质检 (Pre-Alignment Validation) ---")
    logger.info(f"资产池所有 ETF 涉及的全部可能交易日并集天数: {total_calendar_trading_days}")

    has_gaps = False
    for sym, df in raw_data_dict.items():
        sym_dates = set(df.index)
        missing_from_union = sorted(list(all_dates_union - sym_dates))
        report[sym]["missing_dates"] = missing_from_union
        if missing_from_union:
            has_gaps = True
            sample_gaps = [d.date() for d in missing_from_union[:5]]
            logger.warning(
                f"[{sym}] 原始数据相比交易日全集存在 {len(missing_from_union)} 天缺口！"
                f"样例缺失日期: {sample_gaps} (禁止静默删除)"
            )
        else:
            logger.info(f"[{sym}] 完整覆盖全部并集交易日 (天数: {len(df)}, {report[sym]['start_date']} -> {report[sym]['end_date']})")

    if not has_gaps:
        logger.info("所有 ETF 原始交易日完全一致，未发现任何单边缺失天数。")

    return report

def align_universe_data(data_dict: Dict[str, pd.DataFrame]) -> Dict[str, pd.DataFrame]:
    """
    严格对齐资产池中所有 ETF 的有效交易日，取交易日的严格交集 (Inner Join)。
    严禁通过 forward-fill 虚构停牌或休市日数据。
    若存在日期不一致，详细记录各资产缺失的日期差异。
    """
    if not data_dict:
        raise DataValidationError("资产池数据字典为空，无法对齐。")

    # 首先执行独立的前置完整性质检
    pre_alignment_validation(data_dict)

    # 获取所有 ETF 的公共日期集合
    common_dates = None
    all_dates_by_sym = {}

    for sym, df in data_dict.items():
        all_dates_by_sym[sym] = set(df.index)
        if common_dates is None:
            common_dates = set(df.index)
        else:
            common_dates = common_dates.intersection(set(df.index))

    if not common_dates:
        raise DataValidationError("资产池所有 ETF 之间没有共同的有效交易日期，无法进行跨资产动量比较！")

    sorted_common_dates = pd.DatetimeIndex(sorted(list(common_dates)))

    # 截取公共日期的数据切片
    aligned_dict = {}
    for sym, df in data_dict.items():
        aligned_df = df.loc[sorted_common_dates].copy()
        validate_single_etf_data(aligned_df, sym)
        aligned_dict[sym] = aligned_df

    logger.info(
        f"资产池数据对齐完成: 共 {len(aligned_dict)} 只 ETF，公共交易日天数: {len(sorted_common_dates)} "
        f"({sorted_common_dates[0].date()} -> {sorted_common_dates[-1].date()})"
    )
    return aligned_dict
