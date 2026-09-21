import pytest
import pandas as pd
from src.signals.selector import select_top_assets

def test_selector_zero_passed_cash_fallback():
    """
    【场景 1】：0 个资产通过过滤 -> 保持 100% 现金，返回空字典。
    """
    mom = pd.Series({"SPY": 0.15, "QQQ": 0.20, "GLD": 0.05})
    trend = pd.Series({"SPY": False, "QQQ": False, "GLD": False})

    weights = select_top_assets(mom, trend, top_n=3, verbose=False)
    assert weights == {}, "无资产通过趋势过滤时，应返回空字典表示全现金！"

def test_selector_one_passed_single_asset():
    """
    【场景 2】：1 个资产通过过滤 -> 100% 投资该资产。
    """
    mom = pd.Series({"SPY": 0.15, "QQQ": 0.20, "GLD": 0.05})
    trend = pd.Series({"SPY": False, "QQQ": True, "GLD": False})

    weights = select_top_assets(mom, trend, top_n=3, verbose=False)
    assert weights == {"QQQ": 1.0}
    assert sum(weights.values()) == pytest.approx(1.0)

def test_selector_two_passed_equal_split():
    """
    【场景 3】：2 个资产通过过滤 -> 各 50% 等权配置。
    """
    mom = pd.Series({"SPY": 0.15, "QQQ": 0.20, "GLD": 0.05, "TLT": 0.01})
    trend = pd.Series({"SPY": True, "QQQ": True, "GLD": False, "TLT": False})

    weights = select_top_assets(mom, trend, top_n=3, verbose=False)
    assert len(weights) == 2
    assert weights["SPY"] == pytest.approx(0.5)
    assert weights["QQQ"] == pytest.approx(0.5)
    assert sum(weights.values()) == pytest.approx(1.0)

def test_selector_three_passed_one_third_split():
    """
    【场景 4】：3 个资产通过过滤 -> 各 1/3 (约 33.33%) 等权配置。
    """
    mom = pd.Series({"SPY": 0.15, "QQQ": 0.20, "GLD": 0.05, "TLT": 0.01})
    trend = pd.Series({"SPY": True, "QQQ": True, "GLD": True, "TLT": False})

    weights = select_top_assets(mom, trend, top_n=3, verbose=False)
    assert len(weights) == 3
    assert set(weights.keys()) == {"SPY", "QQQ", "GLD"}
    for w in weights.values():
        assert w == pytest.approx(1.0 / 3.0)
    assert sum(weights.values()) == pytest.approx(1.0)

def test_selector_more_than_three_passed_top3_ranking():
    """
    【场景 5】：> 3 个资产通过过滤 (例如 5 个通过) -> 严格按动量降序取前 3 名，各 1/3。
    """
    mom = pd.Series({
        "SPY": 0.12,
        "QQQ": 0.25,   # 排名 1
        "IWM": 0.18,   # 排名 2
        "EFA": 0.08,
        "GLD": 0.15,   # 排名 3
        "TLT": -0.05
    })
    trend = pd.Series({
        "SPY": True,
        "QQQ": True,
        "IWM": True,
        "EFA": True,
        "GLD": True,
        "TLT": False
    })

    weights = select_top_assets(mom, trend, top_n=3, verbose=False)
    
    # 5 个通过 (QQQ 0.25, IWM 0.18, GLD 0.15, SPY 0.12, EFA 0.08)
    # 前 3 名必须是 QQQ, IWM, GLD
    assert len(weights) == 3
    assert set(weights.keys()) == {"QQQ", "IWM", "GLD"}
    # SPY 和 EFA 虽然通过，但因动量低于前3名而被剔除
    assert "SPY" not in weights
    assert "EFA" not in weights
    assert sum(weights.values()) == pytest.approx(1.0)
