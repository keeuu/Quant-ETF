"""Unit tests for deterministic asset selection and portfolio weighting."""
from __future__ import annotations

import math
import pytest

from research_v2.contracts import UNIVERSE
from research_v2.signals import select_top_assets


def test_selection_zero_eligible():
    """When 0 assets pass trend, portfolio must be 100% cash."""
    moms = {sym: 0.15 for sym in UNIVERSE}
    trends = {sym: False for sym in UNIVERSE}

    res = select_top_assets(moms, trends)
    assert res["eligible_symbols"] == []
    assert res["selected_symbols"] == []
    assert res["target_weights"]["CASH"] == 1.0
    for sym in UNIVERSE:
        assert res["target_weights"][sym] == 0.0


def test_selection_one_eligible():
    """When 1 asset passes trend, 100% is allocated to that asset."""
    moms = {sym: 0.05 for sym in UNIVERSE}
    trends = {sym: False for sym in UNIVERSE}
    trends["QQQ"] = True

    res = select_top_assets(moms, trends)
    assert res["eligible_symbols"] == ["QQQ"]
    assert res["selected_symbols"] == ["QQQ"]
    assert res["target_weights"]["QQQ"] == 1.0
    assert res["target_weights"]["CASH"] == 0.0


def test_selection_two_eligible():
    """When 2 assets pass trend, 50% each is allocated."""
    moms = {sym: 0.05 for sym in UNIVERSE}
    moms["SPY"] = 0.20
    moms["GLD"] = 0.10
    trends = {sym: False for sym in UNIVERSE}
    trends["SPY"] = True
    trends["GLD"] = True

    res = select_top_assets(moms, trends)
    assert res["eligible_symbols"] == ["SPY", "GLD"]
    assert res["selected_symbols"] == ["SPY", "GLD"]
    assert res["target_weights"]["SPY"] == 0.5
    assert res["target_weights"]["GLD"] == 0.5
    assert res["target_weights"]["CASH"] == 0.0


def test_selection_eight_eligible_top3():
    """When all 8 pass trend, Top 3 receive 1/3 each and remaining 5 receive 0."""
    # Descending momentum: QQQ(0.35) > SPY(0.30) > IWM(0.25) > others
    moms = {
        "SPY": 0.30, "QQQ": 0.35, "IWM": 0.25, "EFA": 0.20,
        "EEM": 0.15, "GLD": 0.10, "TLT": 0.05, "IEF": 0.01,
    }
    trends = {sym: True for sym in UNIVERSE}

    res = select_top_assets(moms, trends)
    assert res["selected_symbols"] == ["QQQ", "SPY", "IWM"]
    assert math.isclose(res["target_weights"]["QQQ"], 1.0 / 3.0)
    assert math.isclose(res["target_weights"]["SPY"], 1.0 / 3.0)
    assert math.isclose(res["target_weights"]["IWM"], 1.0 / 3.0)
    assert res["target_weights"]["EFA"] == 0.0
    assert res["target_weights"]["CASH"] == 0.0


def test_selection_tie_break_universe_order():
    """When multiple assets tie in momentum, universe order breaks the tie."""
    # SPY, QQQ, and IWM all tie at 0.20, but EFA is higher at 0.25
    moms = {
        "SPY": 0.20, "QQQ": 0.20, "IWM": 0.20, "EFA": 0.25,
        "EEM": 0.10, "GLD": 0.10, "TLT": 0.10, "IEF": 0.10,
    }
    trends = {sym: True for sym in UNIVERSE}

    res = select_top_assets(moms, trends)
    # Top 1 is EFA(0.25). Spots 2 and 3 must go to SPY then QQQ by universe order!
    assert res["selected_symbols"] == ["EFA", "SPY", "QQQ"]


def test_selection_rejects_nan_and_inf():
    """NaN and Inf momentum cannot be selected even if trend passes."""
    moms = {
        "SPY": float("nan"),
        "QQQ": float("inf"),
        "IWM": 0.10,
    }
    trends = {sym: True for sym in UNIVERSE}

    res = select_top_assets(moms, trends)
    assert "SPY" not in res["eligible_symbols"]
    assert "QQQ" not in res["eligible_symbols"]
    assert res["eligible_symbols"] == ["IWM"]
    assert res["selected_symbols"] == ["IWM"]
