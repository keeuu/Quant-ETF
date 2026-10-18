import pytest
import os
import shutil
import pandas as pd
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone

from paper_v2.config import PAPER_V2_STATE_DIR, MODELS, ALL_MODELS
from paper_v2.state import PaperState
from paper_v2.journal import AppendOnlyJournal
from paper_v2.phases import run_signal_phase, run_execution_phase
from research_v2.contracts import UNIVERSE

@pytest.fixture(autouse=True)
def clean_state():
    if os.path.exists(PAPER_V2_STATE_DIR):
        shutil.rmtree(PAPER_V2_STATE_DIR)
    os.makedirs(PAPER_V2_STATE_DIR, exist_ok=True)
    yield
    if os.path.exists(PAPER_V2_STATE_DIR):
        shutil.rmtree(PAPER_V2_STATE_DIR)

def create_mock_bars(dates):
    bars = {}
    for sym in UNIVERSE:
        df = pd.DataFrame(index=pd.DatetimeIndex(dates))
        df["open"] = 100.0
        df["close"] = 100.0
        df["high"] = 105.0
        df["low"] = 95.0
        df["adj_close"] = 100.0
        df["volume"] = 1000
        bars[sym] = df
    return bars

@pytest.fixture
def mock_fetch():
    with patch("paper_v2.phases.fetch_market_data") as mock:
        yield mock

def test_no_broker_imports():
    import sys
    import paper_v2
    for mod in sys.modules.keys():
        assert "alpaca" not in mod.lower()

def test_portfolio_isolation():
    state = PaperState()
    assert len(state._state["models"]) == len(ALL_MODELS)  # 18 base models + CAP_DEF_M shadow
    m1 = MODELS[0].model_id
    m2 = MODELS[1].model_id
    state.update_model_state(m1, {"cash": 50000})
    assert state.get_model_state(m1)["cash"] == 50000
    assert state.get_model_state(m2)["cash"] == 100000

def test_idempotency(mock_fetch):
    from research_v2.calendar import get_trading_calendar
    cal = get_trading_calendar()
    dates = cal.sessions_in_range("2020-01-01", "2021-01-01")
    date_strs = dates.strftime("%Y-%m-%d").tolist()
    
    mock_fetch.return_value = (create_mock_bars(date_strs), "mock_hash")
    
    # We must patch build_weekly_schedule to return mock events
    with patch("paper_v2.phases.build_weekly_schedule") as mock_ws, \
         patch("paper_v2.phases.build_monthly_matched_schedule") as mock_ms, \
         patch("paper_v2.phases.compute_model_signals_for_events") as mock_cs, \
         patch("paper_v2.phases.compute_matched_monthly_signals") as mock_cms:
        
        from research_v2.contracts import PeriodEvent, SignalFrame
        
        e1 = PeriodEvent(
            period_id="W1",
            signal_session="2020-12-24",
            signal_close_ts="2020-12-24T16:00:00Z",
            execution_session="2020-12-28",
            execution_open_ts="2020-12-28T09:30:00Z",
            feature_cutoff="2020-12-24T16:00:00Z",
            status="PENDING"
        )
        mock_ws.return_value = [e1]
        mock_ms.return_value = []
        
        sf = SignalFrame(
            model_id="mock",
            rows=[{"signal_session": "2020-12-24", "weights": {"SPY": 0.5, "TLT": 0.5}}],
            lineage={}
        )
        mock_cs.return_value = sf
        mock_cms.return_value = sf
        
        run_signal_phase("2020-12-25")
        
        state = PaperState()
        pending = state.get_model_state("W_M06_T36")["pending_order"]
        assert pending is not None
        assert pending["target_weights"] == {"SPY": 0.5, "TLT": 0.5}
        
        # Test duplicate signal
        run_signal_phase("2020-12-25")
        # should just return early, idempotency
        
        # Execution phase
        run_execution_phase("2020-12-28")
        state = PaperState()
        assert state.get_model_state("W_M06_T36")["pending_order"] is None
        assert state.get_model_state("W_M06_T36")["cash"] < 100000
        
        # Test duplicate execution
        run_execution_phase("2020-12-28") # no-op

def test_all_cash(mock_fetch):
    from research_v2.calendar import get_trading_calendar
    cal = get_trading_calendar()
    dates = cal.sessions_in_range("2020-01-01", "2021-01-01")
    date_strs = dates.strftime("%Y-%m-%d").tolist()
    mock_fetch.return_value = (create_mock_bars(date_strs), "mock_hash")
    
    with patch("paper_v2.phases.build_weekly_schedule") as mock_ws, \
         patch("paper_v2.phases.build_monthly_matched_schedule") as mock_ms, \
         patch("paper_v2.phases.compute_model_signals_for_events") as mock_cs:
        
        from research_v2.contracts import PeriodEvent, SignalFrame
        
        e1 = PeriodEvent(
            period_id="W1",
            signal_session="2020-12-24",
            signal_close_ts="2020-12-24T16:00:00Z",
            execution_session="2020-12-28",
            execution_open_ts="2020-12-28T09:30:00Z",
            feature_cutoff="2020-12-24T16:00:00Z",
            status="PENDING"
        )
        mock_ws.return_value = [e1]
        mock_ms.return_value = []
        
        sf = SignalFrame(
            model_id="mock",
            rows=[{"signal_session": "2020-12-24", "weights": {"CASH": 1.0}}],
            lineage={}
        )
        mock_cs.return_value = sf
        
        run_signal_phase("2020-12-25")
        run_execution_phase("2020-12-28")
        
        state = PaperState()
        # Should be exactly 100k
        assert state.get_model_state("W_M06_T36")["cash"] == 100000.0

def test_restart_recovery():
    # If state is corrupted or restart happens, it should reload correctly.
    state1 = PaperState()
    state1.update_model_state("W_M06_T36", {"cash": 999})
    state1.save()
    
    state2 = PaperState()
    assert state2.get_model_state("W_M06_T36")["cash"] == 999

def test_missing_open_price(mock_fetch):
    from research_v2.calendar import get_trading_calendar
    cal = get_trading_calendar()
    dates = cal.sessions_in_range("2020-01-01", "2021-01-01")
    date_strs = dates.strftime("%Y-%m-%d").tolist()
    
    bars = create_mock_bars(date_strs)
    # Remove one open price
    bars["SPY"].loc["2020-12-28", "open"] = pd.NA
    mock_fetch.return_value = (bars, "mock_hash")
    
    with patch("paper_v2.phases.build_weekly_schedule") as mock_ws, \
         patch("paper_v2.phases.build_monthly_matched_schedule") as mock_ms, \
         patch("paper_v2.phases.compute_model_signals_for_events") as mock_cs:
        
        from research_v2.contracts import PeriodEvent, SignalFrame
        
        e1 = PeriodEvent(
            period_id="W1",
            signal_session="2020-12-24",
            signal_close_ts="2020-12-24T16:00:00Z",
            execution_session="2020-12-28",
            execution_open_ts="2020-12-28T09:30:00Z",
            feature_cutoff="2020-12-24T16:00:00Z",
            status="PENDING"
        )
        mock_ws.return_value = [e1]
        mock_ms.return_value = []
        sf = SignalFrame(model_id="mock", rows=[{"signal_session": "2020-12-24", "weights": {"SPY": 1.0}}], lineage={})
        mock_cs.return_value = sf
        
        run_signal_phase("2020-12-25")
        run_execution_phase("2020-12-28")
        
        state = PaperState()
        pending = state.get_model_state("W_M06_T36")["pending_order"]
        assert pending["status"] == "FAILED"
        assert pending["reason"] == "missing open price"
