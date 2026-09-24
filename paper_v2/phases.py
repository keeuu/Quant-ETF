import uuid
import hashlib
from datetime import datetime, timezone
from typing import Dict, Any, List

import pandas as pd

from research_v2.calendar import build_session_table, get_trading_calendar
from research_v2.schedule import build_weekly_schedule, build_monthly_matched_schedule
from research_v2.signals import compute_model_signals_for_events, compute_matched_monthly_signals
from research_v2.ledger import execute_rebalance
from research_v2.contracts import PeriodEvent

from .config import MODELS, SIGNAL_JOURNAL_PATH, EXEC_JOURNAL_PATH, NAV_JOURNAL_PATH, UNIVERSE, COST_BPS
from .state import PaperState
from .journal import AppendOnlyJournal
from .market_data import fetch_market_data

def _hash_dict(d: dict) -> str:
    return hashlib.sha256(str(sorted(d.items())).encode('utf-8')).hexdigest()

def run_signal_phase(as_of_date: str):
    """
    Run signal generation up to as_of_date.
    No historical backfilling: only generate signals if they are strictly after activation timestamp.
    However, for 'IMPLEMENTATION_READY' state, we might not have activated yet, so we just dry-run or process current week.
    """
    state = PaperState()
    
    # In a real environment, as_of_date would be "now".
    bars, data_hash = fetch_market_data(as_of_date)
    session_dates = build_session_table(start="2005-01-03", end=as_of_date)["session_date"].tolist()
    
    # We only care about the latest event
    weekly_events = build_weekly_schedule(session_dates, as_of_date)
    monthly_events = build_monthly_matched_schedule(session_dates, as_of_date, weekly_events)
    
    # The actual pending events that need signals:
    # A valid new signal is for the event where `signal_close_ts <= as_of_date` and we haven't generated it yet.
    # For Paper V2, we just take the last completed period event.
    
    if not weekly_events:
        return
        
    latest_weekly = weekly_events[-1]
    latest_monthly = monthly_events[-1] if monthly_events else None
    
    signal_journal = AppendOnlyJournal(SIGNAL_JOURNAL_PATH)
    existing_signals = {(s["model_id"], s["signal_session"]) for s in signal_journal.read_all()}
    
    for model in MODELS:
        if model.cadence == "W":
            event = latest_weekly
        else:
            if not latest_monthly:
                continue
            event = latest_monthly
            
        if (model.model_id, event.signal_session) in existing_signals:
            continue
            
        # Check idempotency in pending state as well
        model_state = state.get_model_state(model.model_id)
        if model_state["pending_order"] and model_state["pending_order"]["signal_session"] == event.signal_session:
             continue
             
        # Compute signal
        if model.cadence == "W":
            frame = compute_model_signals_for_events(
                bars, session_dates, [event], model.model_id, model.momentum_months, model.trend_weeks
            )
        else:
            w_model_id = f"W_M{model.momentum_months:02d}_T{model.trend_weeks}"
            # Need the corresponding weekly signal
            # This requires computing the weekly signals up to this monthly event
            w_frame = compute_model_signals_for_events(
                bars, session_dates, [e for e in weekly_events if e.signal_session <= event.source_signal_session],
                w_model_id, model.momentum_months, model.trend_weeks
            )
            frame = compute_matched_monthly_signals(w_frame, [event], model.model_id)
            
        row = frame.rows[event.signal_session]
        targets = {sym: row[sym] for sym in UNIVERSE if sym in row}
        cash_weight = row.get("CASH", 1.0 - sum(targets.values()))
        
        # We must drop CASH from risky weights
        if "CASH" in targets:
            del targets["CASH"]
            
        signal_hash = _hash_dict(targets)
        
        signal_event = {
            "event_id": str(uuid.uuid4()),
            "model_id": model.model_id,
            "signal_session": event.signal_session,
            "expected_execution_session": event.execution_session,
            "data_cutoff": event.signal_close_ts,
            "selected_symbols": [sym for sym, w in targets.items() if w > 0],
            "weights": targets,
            "cash_weight": cash_weight,
            "source_data_hash": data_hash,
            "strategy_commit": "HEAD", # Mocked
            "config_hash": "CFG_HASH",
            "signal_hash": signal_hash,
            "created_at": datetime.now(timezone.utc).isoformat()
        }
        
        signal_journal.append(signal_event)
        
        # Set pending order
        state.set_pending_order(model.model_id, {
            "pending_order_id": signal_event["event_id"],
            "model_id": model.model_id,
            "signal_hash": signal_hash,
            "signal_session": event.signal_session,
            "expected_execution_session": event.execution_session,
            "target_weights": targets,
            "status": "PENDING"
        })
        state.save()

def run_execution_phase(as_of_date: str):
    """
    Execute any pending orders that have reached their execution session.
    """
    state = PaperState()
    bars, _ = fetch_market_data(as_of_date)
    
    exec_journal = AppendOnlyJournal(EXEC_JOURNAL_PATH)
    nav_journal = AppendOnlyJournal(NAV_JOURNAL_PATH)
    
    for model in MODELS:
        model_state = state.get_model_state(model.model_id)
        pending = model_state.get("pending_order")
        
        if not pending or pending["status"] != "PENDING":
            continue
            
        expected_exec = pending["expected_execution_session"]
        
        # Ensure we have reached the execution session and have open prices
        if expected_exec > as_of_date:
            continue
            
        # Get execution prices
        exec_prices = {}
        missing_price = False
        for sym in UNIVERSE:
            df = bars[sym]
            if expected_exec in df.index.strftime("%Y-%m-%d"):
                exec_prices[sym] = df.loc[expected_exec, "open"]
                if pd.isna(exec_prices[sym]):
                     missing_price = True
            else:
                missing_price = True
                
        if missing_price:
            # FAIL_CLOSED
            pending["status"] = "FAILED"
            pending["reason"] = "missing open price"
            state.set_pending_order(model.model_id, pending)
            state.save()
            continue
            
        # Execute rebalance using Research V2 ledger
        try:
            new_shares, new_cash, trades, record = execute_rebalance(
                signal_session=pending["signal_session"],
                execution_session=expected_exec,
                model_id=model.model_id,
                current_shares=model_state["shares"],
                current_cash=model_state["cash"],
                execution_open_prices=exec_prices,
                target_weights=pending["target_weights"],
                cost_bps=COST_BPS,
                universe=UNIVERSE
            )
        except Exception as e:
            pending["status"] = "FAILED"
            pending["reason"] = str(e)
            state.set_pending_order(model.model_id, pending)
            state.save()
            continue
            
        # Create execution event
        exec_event = {
            "signal_hash": pending["signal_hash"],
            "execution_session": expected_exec,
            "execution_timestamp": datetime.now(timezone.utc).isoformat(),
            "prices": exec_prices,
            "shares_before": model_state["shares"],
            "shares_after": new_shares,
            "cash_before": model_state["cash"],
            "cash_after": new_cash,
            "fees": record.fee,
            "slippage": 0.0,
            "turnover": record.one_way_turnover,
            "status": "EXECUTED"
        }
        
        exec_journal.append(exec_event)
        
        # Update state
        model_state["shares"] = new_shares
        model_state["cash"] = new_cash
        model_state["fees"] += record.fee
        model_state["turnover"] += record.one_way_turnover
        model_state["last_signal"] = pending["signal_session"]
        model_state["last_execution"] = expected_exec
        model_state["nav"] = record.V_post
        model_state["pending_order"] = None
        
        state.update_model_state(model.model_id, model_state)
        state.save()
        
        # Update daily NAV journal
        nav_event = {
            "date": expected_exec,
            "model_id": model.model_id,
            "cash": new_cash,
            "market_value": record.V_post - new_cash,
            "nav": record.V_post,
            "drawdown": 0.0, # Not calculated online
            "positions": new_shares
        }
        nav_journal.append(nav_event)
