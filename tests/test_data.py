import pytest
import pandas as pd
import numpy as np
from src.data.fetcher import fetch_universe_data, get_monthly_dataset, calculate_adjusted_ohlc
from src.data.validator import validate_single_etf_data, align_universe_data, DataValidationError
from config.config import UNIVERSE

def test_calculate_adjusted_ohlc_math():
    """
    测试复权代理公式: adj_open = open * (adj_close / close)。
    """
    sample_df = pd.DataFrame({
        "open": [100.0, 200.0],
        "high": [105.0, 205.0],
        "low": [95.0, 195.0],
        "close": [100.0, 200.0],
        "adj_close": [90.0, 160.0], # 发生除权分红
        "volume": [1000, 2000]
    }, index=pd.date_range("2024-01-01", periods=2))

    adj_df = calculate_adjusted_ohlc(sample_df)
    
    # 验证第一天: factor = 90 / 100 = 0.9 -> adj_open = 90.0
    assert adj_df.loc[sample_df.index[0], "adj_open"] == pytest.approx(90.0)
    # 验证第二天: factor = 160 / 200 = 0.8 -> adj_open = 160.0
    assert adj_df.loc[sample_df.index[1], "adj_open"] == pytest.approx(160.0)

def test_validator_detects_nan_and_bad_price():
    """
    测试校验器能准确拦截包含 NaN 和非正价格的数据。
    """
    bad_df_nan = pd.DataFrame({
        "open": [100.0, np.nan],
        "high": [105.0, 105.0],
        "low": [95.0, 95.0],
        "close": [100.0, 100.0],
        "adj_close": [100.0, 100.0],
        "adj_open": [100.0, 100.0],
        "volume": [1000, 1000]
    }, index=pd.date_range("2024-01-01", periods=2))

    with pytest.raises(DataValidationError, match="存在 NaN 空值"):
        validate_single_etf_data(bad_df_nan, "TEST_NAN")

    bad_df_negative = pd.DataFrame({
        "open": [100.0, -10.0],
        "high": [105.0, 105.0],
        "low": [95.0, 95.0],
        "close": [100.0, 100.0],
        "adj_close": [100.0, 100.0],
        "adj_open": [100.0, -10.0],
        "volume": [1000, 1000]
    }, index=pd.date_range("2024-01-01", periods=2))

    with pytest.raises(DataValidationError, match="非法价格"):
        validate_single_etf_data(bad_df_negative, "TEST_NEG")

def test_real_cached_universe_data_integrity():
    """
    测试本地已缓存的真实 8 只 ETF 数据完整性与公共交易日对齐。
    """
    universe_data = fetch_universe_data(UNIVERSE)
    assert len(universe_data) == 8
    
    # 验证全部 8 只 ETF 交易日长度完全相等且严格对齐
    first_sym = UNIVERSE[0]
    base_dates = universe_data[first_sym].index
    for sym in UNIVERSE[1:]:
        assert universe_data[sym].index.equals(base_dates), f"{sym} 与 {first_sym} 交易日未能完美对齐！"

    monthly_close, monthly_open = get_monthly_dataset(universe_data)
    assert len(monthly_close) > 100
    assert len(monthly_close) == len(monthly_open)
    assert not monthly_close.isna().any().any(), "月度信号收盘价存在 NaN！"
    assert not monthly_open.isna().any().any(), "月度执行开盘价存在 NaN！"
