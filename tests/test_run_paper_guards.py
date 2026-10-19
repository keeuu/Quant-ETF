"""Tests for scripts/run_paper.py helpers: signal consistency, calendar probes, kill-switch.

All broker interaction is mocked; no network and no order is ever submitted.
"""
from datetime import date
from unittest.mock import MagicMock

import numpy as np
import pandas as pd
import pytest

from scripts import run_paper
from src.broker.alpaca_client import SecurityException
from src.signals.selector import select_top_assets
from src.signals.momentum import calculate_12_1_momentum
from src.signals.trend import check_trend_filter


def _monthly_close(drifts: dict) -> pd.DataFrame:
    idx = pd.date_range("2020-01-31", periods=30, freq="ME")
    t = np.arange(len(idx))
    return pd.DataFrame({s: 100.0 * np.exp(d * t) for s, d in drifts.items()}, index=idx)


DRIFTS = {"A": 0.020, "B": 0.015, "C": 0.010, "D": 0.005, "E": -0.01}


def _target(mc):
    return select_top_assets(
        calculate_12_1_momentum(mc).loc[mc.index[-1]],
        check_trend_filter(mc).loc[mc.index[-1]],
        verbose=False,
    )


def test_selection_check_not_hardcoded_passes_for_any_universe():
    mc = _monthly_close(DRIFTS)
    target = _target(mc)
    assert set(target) == {"A", "B", "C"}  # not the old hardcoded EEM/IWM/QQQ
    expected = run_paper.verify_research_selection(mc, target)
    assert set(expected) == set(target)


def test_selection_check_raises_on_drift():
    mc = _monthly_close(DRIFTS)
    with pytest.raises(RuntimeError, match="RESEARCH_SIGNAL_DRIFT"):
        run_paper.verify_research_selection(mc, {"A": 1 / 3, "B": 1 / 3, "D": 1 / 3})
    with pytest.raises(RuntimeError, match="RESEARCH_SIGNAL_DRIFT"):
        run_paper.verify_research_selection(mc, {"A": 0.5, "B": 0.25, "C": 0.25})


def test_calendar_probe_relative_to_run_date():
    client = MagicMock()
    client.get_calendar.side_effect = lambda start_date, end_date: [
        {"date": pd.Timestamp(start_date), "open": None, "close": None}
    ]
    info = run_paper.probe_broker_calendar(client, date(2027, 12, 17))
    calls = [c.kwargs["start_date"] for c in client.get_calendar.call_args_list]
    assert calls == ["2027-12-01", "2028-01-01"]
    assert info == {
        "current_month_first_trading": "2027-12-01",
        "next_month_first_trading": "2028-01-01",
    }
    assert "2026-09" not in str(client.get_calendar.call_args_list)


def test_kill_switch_self_test_never_touches_broker(monkeypatch):
    client = MagicMock()
    monkeypatch.setattr(run_paper, "AlpacaPaperClient", lambda *a, **k: client)
    assert run_paper.kill_switch_self_test(False).startswith("PASS")
    client.submit_order.assert_not_called()


def test_kill_switch_self_test_fails_closed_if_flag_enabled():
    with pytest.raises(SecurityException):
        run_paper.kill_switch_self_test(True)


def test_run_paper_source_has_no_test_order_call():
    import inspect
    src = inspect.getsource(run_paper)
    assert "submit_order(" not in src
    assert "expected_top3" not in src
