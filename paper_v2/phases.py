import sys
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
from .asof import normalize_as_of
from .runinfo import strategy_commit, config_hash

def _hash_dict(d: dict) -> str:
    return hashlib.sha256(str(sorted(d.items())).encode('utf-8')).hexdigest()


def _research_bars(bars: Dict[str, pd.DataFrame]) -> Dict[str, pd.DataFrame]:
    """Ensure each frame has the research_v2 'Date' (YYYY-MM-DD string) column."""
    out = {}
    for sym, df in bars.items():
        if "Date" not in df.columns:
            df = df.copy()
            df.insert(0, "Date", pd.DatetimeIndex(df.index).strftime("%Y-%m-%d"))
            df = df.reset_index(drop=True)
        out[sym] = df
    return out


def _find_row(frame, signal_session: str):
    """rows is a list of dicts (one per weekly/monthly event); find the one for signal_session."""
    for row in frame.rows:
        if row["signal_session"] == signal_session:
            return row
    return None


def run_signal_phase(as_of_date: str):
    """
    Run signal generation as of a market date (YYYY-MM-DD, after that session's close).
    Generates the signal for the latest completed weekly / matched-monthly event, once per
    (model, signal_session); no historical backfilling.
    """
    as_of_date, as_of_ts = normalize_as_of(as_of_date)
    state = PaperState()

    bars, data_hash = fetch_market_data(as_of_date)
    bars = _research_bars(bars)
    session_dates = build_session_table(start="2005-01-03", end=as_of_date)["session_date"].tolist()

    weekly_events = build_weekly_schedule(session_dates, as_of_ts)
    monthly_events = build_monthly_matched_schedule(session_dates, as_of_ts, weekly_events)

    if not weekly_events:
        return

    latest_weekly = weekly_events[-1]
    latest_monthly = monthly_events[-1] if monthly_events else None

    signal_journal = AppendOnlyJournal(SIGNAL_JOURNAL_PATH)
    existing_signals = {(s["model_id"], s["signal_session"]) for s in signal_journal.read_all()}
    commit = strategy_commit()
    cfg_hash = config_hash()

    # Weekly signal frames need the FULL weekly event history (momentum skips the first week,
    # trend needs K weekly closes). Cache per (momentum, trend); monthly models reuse them.
    weekly_frames = {}

    def weekly_frame(m: int, t: int):
        key = (m, t)
        if key not in weekly_frames:
            weekly_frames[key] = compute_model_signals_for_events(
                bars, session_dates, weekly_events, f"W_M{m:02d}_T{t}", m, t
            )
        return weekly_frames[key]

    for model in MODELS:
        if model.cadence == "W":
            event = latest_weekly
        else:
            if not latest_monthly:
                continue
            event = latest_monthly

        if (model.model_id, event.signal_session) in existing_signals:
            continue

        model_state = state.get_model_state(model.model_id)
        if model_state["pending_order"] and model_state["pending_order"]["signal_session"] == event.signal_session:
            continue

        w_frame = weekly_frame(model.momentum_months, model.trend_weeks)
        if model.cadence == "W":
            frame = w_frame
        else:
            frame = compute_matched_monthly_signals(w_frame, [event], model.model_id)

        row = _find_row(frame, event.signal_session)
        if row is None:
            print(f"WARNING: no signal row for {model.model_id} @ {event.signal_session} "
                  f"(insufficient history?); skipped", file=sys.stderr)
            continue

        weights = row["weights"]
        targets = {sym: float(weights[sym]) for sym in UNIVERSE if sym in weights}
        cash_weight = float(weights.get("CASH", 1.0 - sum(targets.values())))

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
            "strategy_commit": commit,
            "config_hash": cfg_hash,
            "signal_hash": signal_hash,
            "created_at": datetime.now(timezone.utc).isoformat()
        }

        signal_journal.append(signal_event)

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


def _open_price(df: pd.DataFrame, date: str):
    """Raw open on `date` ('YYYY-MM-DD'), or None if the session is absent."""
    if "Date" in df.columns:
        hit = df.loc[df["Date"] == date, "open"]
    else:
        hit = df.loc[pd.DatetimeIndex(df.index).strftime("%Y-%m-%d") == date, "open"]
    return None if hit.empty else hit.iloc[0]


def _dividends_in_window(df: pd.DataFrame, start_excl, end_incl: str):
    """Yield (ex_date, per_share_dividend) with start_excl < ex_date <= end_incl, ascending."""
    if "dividends" not in df.columns:
        return []
    dates = df["Date"] if "Date" in df.columns else pd.Series(pd.DatetimeIndex(df.index).strftime("%Y-%m-%d"), index=df.index)
    mask = (df["dividends"].fillna(0.0) > 0) & (dates <= end_incl)
    if start_excl is not None:
        mask &= dates > start_excl
    return [(d, float(v)) for d, v in zip(dates[mask], df.loc[mask, "dividends"])]


def credit_dividends(model_state: dict, bars: Dict[str, pd.DataFrame], end_incl: str, exec_journal=None,
                     model_id: str = ""):
    """Credit cash dividends (per-share * shares held) on each ex-date in (last_dividend_date, end_incl].

    Idempotent: every credited (symbol, ex-date) is recorded in model_state['dividends_credited'],
    and last_dividend_date advances, so reruns never double count. Shares held are the model's
    shares at the time of the call; callers invoke this before a rebalance that executes at the
    ex-date open, i.e. holders at the prior close receive the dividend (ex-date open trades do not).
    """
    credited = model_state.setdefault("dividends_credited", {})
    start = model_state.get("last_dividend_date")
    total = 0.0
    events = []
    for sym, df in bars.items():
        shares = model_state["shares"].get(sym, 0.0)
        for ex_date, per_share in _dividends_in_window(df, start, end_incl):
            key = f"{sym}|{ex_date}"
            if key in credited or shares <= 0:
                continue
            amount = per_share * shares
            credited[key] = amount
            total += amount
            events.append({"status": "DIVIDEND_CREDITED", "model_id": model_id, "symbol": sym,
                           "ex_date": ex_date, "per_share": per_share, "shares": shares, "amount": amount})
    if start is None or end_incl > start:
        model_state["last_dividend_date"] = end_incl
    if total:
        model_state["cash"] += total
        model_state["nav"] = model_state.get("nav", 0.0) + total
        model_state["dividends_total"] = model_state.get("dividends_total", 0.0) + total
    if exec_journal is not None:
        for ev in events:
            exec_journal.append(ev)
    return total


def run_execution_phase(as_of_date: str):
    """
    Execute any pending orders that have reached their execution session, crediting cash
    dividends (ex-date basis, from yfinance Dividends) so the ledger is on a total-return basis.
    """
    as_of_date, _ = normalize_as_of(as_of_date)
    state = PaperState()
    bars, _ = fetch_market_data(as_of_date)

    exec_journal = AppendOnlyJournal(EXEC_JOURNAL_PATH)
    nav_journal = AppendOnlyJournal(NAV_JOURNAL_PATH)

    for model in MODELS:
        model_state = state.get_model_state(model.model_id)
        pending = model_state.get("pending_order")

        if pending and pending["status"] == "PENDING" and pending["expected_execution_session"] <= as_of_date:
            # Dividends up to and including the execution date accrue to pre-trade holdings.
            credit_dividends(model_state, bars, pending["expected_execution_session"], exec_journal, model.model_id)
            _execute_pending(state, model, model_state, pending, bars, exec_journal, nav_journal)

        # Dividends after the last execution (or when nothing is pending) on current holdings.
        credit_dividends(model_state, bars, as_of_date, exec_journal, model.model_id)
        state.update_model_state(model.model_id, model_state)
        state.save()


def _execute_pending(state, model, model_state, pending, bars, exec_journal, nav_journal):
    expected_exec = pending["expected_execution_session"]

    exec_prices = {}
    missing_price = False
    for sym in UNIVERSE:
        px = _open_price(bars[sym], expected_exec)
        if px is None or pd.isna(px):
            missing_price = True
        else:
            exec_prices[sym] = float(px)

    if missing_price:
        # FAIL_CLOSED
        pending["status"] = "FAILED"
        pending["reason"] = "missing open price"
        state.set_pending_order(model.model_id, pending)
        return

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
        return

    exec_journal.append({
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
    })

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

    nav_journal.append({
        "date": expected_exec,
        "model_id": model.model_id,
        "cash": new_cash,
        "market_value": record.V_post - new_cash,
        "nav": record.V_post,
        "drawdown": 0.0,  # Not calculated online
        "positions": new_shares
    })
