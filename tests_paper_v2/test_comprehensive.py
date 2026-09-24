import pytest
import os
import shutil
import pandas as pd
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone

from paper_v2.config import PAPER_V2_STATE_DIR, MODELS, COST_BPS
from paper_v2.state import PaperState
from paper_v2.journal import AppendOnlyJournal
from paper_v2.phases import run_signal_phase, run_execution_phase
from research_v2.contracts import UNIVERSE, PeriodEvent, SignalFrame

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

def test_fully_invested(mock_fetch):
    from research_v2.calendar import get_trading_calendar
    cal = get_trading_calendar()
    dates = cal.sessions_in_range("2020-01-01", "2021-01-01")
    date_strs = dates.strftime("%Y-%m-%d").tolist()
    mock_fetch.return_value = (create_mock_bars(date_strs), "mock_hash")

    with patch("paper_v2.phases.build_weekly_schedule") as mock_ws, \
         patch("paper_v2.phases.build_monthly_matched_schedule") as mock_ms, \
         patch("paper_v2.phases.compute_model_signals_for_events") as mock_cs:
        
        e1 = PeriodEvent("W1", "2020-12-24", "2020-12-24T16:00:00Z", "2020-12-28", "2020-12-28T09:30:00Z", "2020-12-24T16:00:00Z", "PENDING")
        mock_ws.return_value = [e1]
        mock_ms.return_value = []
        
        sf = SignalFrame("mock", {"2020-12-24": {"SPY": 1.0}}, {})
        mock_cs.return_value = sf
        
        run_signal_phase("2020-12-25")
        run_execution_phase("2020-12-28")
        
        state = PaperState()
        # 100k capital. 5 bps cost on 100k is 50. Remaining is 99950. 
        # Price is 100. Shares should be 999.5.
        model_state = state.get_model_state("W_M06_T36")
        assert model_state["shares"]["SPY"] > 0
        assert model_state["cash"] < 100
        assert abs(model_state["turnover"] - 1.0) < 1e-5

def test_partial_cash(mock_fetch):
    from research_v2.calendar import get_trading_calendar
    cal = get_trading_calendar()
    dates = cal.sessions_in_range("2020-01-01", "2021-01-01")
    date_strs = dates.strftime("%Y-%m-%d").tolist()
    mock_fetch.return_value = (create_mock_bars(date_strs), "mock_hash")

    with patch("paper_v2.phases.build_weekly_schedule") as mock_ws, \
         patch("paper_v2.phases.build_monthly_matched_schedule") as mock_ms, \
         patch("paper_v2.phases.compute_model_signals_for_events") as mock_cs:
        
        e1 = PeriodEvent("W1", "2020-12-24", "2020-12-24T16:00:00Z", "2020-12-28", "2020-12-28T09:30:00Z", "2020-12-24T16:00:00Z", "PENDING")
        mock_ws.return_value = [e1]
        mock_ms.return_value = []
        
        sf = SignalFrame("mock", {"2020-12-24": {"SPY": 0.5, "CASH": 0.5}}, {})
        mock_cs.return_value = sf
        
        run_signal_phase("2020-12-25")
        run_execution_phase("2020-12-28")
        
        state = PaperState()
        model_state = state.get_model_state("W_M06_T36")
        # Half cash
        assert abs(model_state["cash"] - 50000) < 50

def test_monthly_matched_control(mock_fetch):
    from research_v2.calendar import get_trading_calendar
    cal = get_trading_calendar()
    dates = cal.sessions_in_range("2020-01-01", "2021-01-01")
    date_strs = dates.strftime("%Y-%m-%d").tolist()
    mock_fetch.return_value = (create_mock_bars(date_strs), "mock_hash")

    with patch("paper_v2.phases.build_weekly_schedule") as mock_ws, \
         patch("paper_v2.phases.build_monthly_matched_schedule") as mock_ms, \
         patch("paper_v2.phases.compute_model_signals_for_events") as mock_cs, \
         patch("paper_v2.phases.compute_matched_monthly_signals") as mock_cms:
        
        e1 = PeriodEvent("W1", "2020-12-24", "2020-12-24T16:00:00Z", "2020-12-28", "2020-12-28T09:30:00Z", "2020-12-24T16:00:00Z", "PENDING")
        e_month = PeriodEvent("M1", "2020-12-31", "2020-12-31T16:00:00Z", "2021-01-04", "2021-01-04T09:30:00Z", "2020-12-31T16:00:00Z", "PENDING", source_signal_session="2020-12-24")
        
        mock_ws.return_value = [e1]
        mock_ms.return_value = [e_month]
        
        sf = SignalFrame("mock", {"2020-12-24": {"SPY": 1.0}}, {})
        mock_cs.return_value = sf
        sf_month = SignalFrame("mock", {"2020-12-31": {"SPY": 1.0}}, {})
        mock_cms.return_value = sf_month
        
        run_signal_phase("2021-01-01")
        
        state = PaperState()
        model_state = state.get_model_state("M_M06_T36")
        assert model_state["pending_order"]["signal_session"] == "2020-12-31"

def test_future_open_poisoning(mock_fetch):
    from research_v2.calendar import get_trading_calendar
    cal = get_trading_calendar()
    dates = cal.sessions_in_range("2020-01-01", "2021-01-01")
    date_strs = dates.strftime("%Y-%m-%d").tolist()
    bars = create_mock_bars(date_strs)
    mock_fetch.return_value = (bars, "mock_hash")

    with patch("paper_v2.phases.build_weekly_schedule") as mock_ws, \
         patch("paper_v2.phases.build_monthly_matched_schedule") as mock_ms, \
         patch("paper_v2.phases.compute_model_signals_for_events") as mock_cs:
        
        e1 = PeriodEvent("W1", "2020-12-24", "2020-12-24T16:00:00Z", "2020-12-28", "2020-12-28T09:30:00Z", "2020-12-24T16:00:00Z", "PENDING")
        mock_ws.return_value = [e1]
        mock_ms.return_value = []
        
        sf = SignalFrame("mock", {"2020-12-24": {"SPY": 1.0}}, {})
        mock_cs.return_value = sf
        
        # signal phase is at "2020-12-25"
        # at this time, "2020-12-28" data shouldn't be read for signal
        run_signal_phase("2020-12-25")
        
        journal = AppendOnlyJournal(os.path.join(PAPER_V2_STATE_DIR, "signal_journal.jsonl"))
        evts = journal.read_all()
        assert len(evts) == 9 # 9 weekly models
        
        # Now change future open
        bars["SPY"].loc["2020-12-28", "open"] = 200.0
        
        # run signal phase again on 2020-12-27, signal should remain identical due to idempotency / journal
        run_signal_phase("2020-12-27")
        evts2 = journal.read_all()
        assert len(evts2) == 9 # no new events
