"""Daily Mark-to-Market simulation engine for Research V2.

Strictly executes:
- Yesterday close holdings -> overnight gap into execution open (evaluated on adj_open)
- Self-financing rebalance + fee deduction at execution open
- Intraday holding -> today close valuation on adj_close
- First session return accounts for initial capital $100,000 and initial transaction fee
- Decouples non-rebalance open dependency: missing adj_open on non-rebalance day does NOT fail
- Strict fail-closed on missing valuation adj_close or rebalance execution adj_open
"""
from __future__ import annotations

import math
from typing import Any, Mapping, Sequence
import pandas as pd

from .calendar import get_trading_calendar
from .contracts import (
    DailyNavRecord,
    DatasetView,
    PendingExecutionRecord,
    PositionRecord,
    RebalanceRecord,
    RunResult,
    RunSpec,
    SignalFrame,
    TradeRecord,
    UNIVERSE,
)
from .ledger import execute_rebalance, LedgerError
from .metrics import compute_backtest_metrics


class EngineError(ValueError):
    """Raised when simulation invariants or data constraints fail."""
    pass


def run_backtest(
    dataset_view: DatasetView,
    signals: SignalFrame,
    cost_bps: float,
    initial_nav: float = 100000.0,
    universe: Sequence[str] = UNIVERSE,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    """Run full daily MTM backtest over dataset_view's scored partition.

    Returns:
        (daily_nav_df, positions_df, trades_df, rebalance_df, pending_df, metrics_dict)
    """
    if initial_nav <= 0:
        raise EngineError(f"Initial capital must be positive, got {initial_nav}")

    cal = get_trading_calendar()
    score_start = dataset_view.score_start
    score_end = dataset_view.score_end

    # 1. Determine valid XNYS trading sessions in the scored partition
    sessions = [
        s.strftime("%Y-%m-%d")
        for s in cal.sessions_in_range(pd.Timestamp(score_start), pd.Timestamp(score_end))
    ]
    if not sessions:
        raise EngineError(f"No trading sessions found in partition interval [{score_start}, {score_end}]")

    session_set = set(sessions)

    # 2. Build map of signals eligible for execution within this segment
    # Signals must have status == 'COMPLETE' and execution_session in this segment
    execution_signals: dict[str, dict[str, Any]] = {}
    pending_records: list[PendingExecutionRecord] = []
    # Upstream schedule statuses eligible for pending outside segment
    valid_pending_upstream_statuses = {"COMPLETE", "PENDING"}
    for sig in signals.rows:
        exec_s = sig["execution_session"]
        sig_status = sig.get("status")
        if exec_s > score_end:
            if sig_status in valid_pending_upstream_statuses:
                pending_records.append(PendingExecutionRecord(
                    signal_session=sig["signal_session"],
                    execution_session=exec_s,
                    model_id=signals.model_id,
                    partition=dataset_view.partition,
                    status="PENDING_OUTSIDE_SEGMENT",
                    reason="EXECUTION_SESSION_AFTER_SEGMENT_END"
                ))
        elif sig_status == "COMPLETE":
            if exec_s in session_set:
                execution_signals[exec_s] = sig

    # 3. Pre-index prices by (symbol, date)
    open_prices: dict[str, dict[str, float]] = {sym: {} for sym in universe}
    close_prices: dict[str, dict[str, float]] = {sym: {} for sym in universe}

    for sym in universe:
        df = dataset_view.bars[sym]
        for _, row in df.iterrows():
            d = str(row["Date"])
            adj_o = row.get("adj_open")
            adj_c = row.get("adj_close")
            if adj_o is not None and not math.isnan(adj_o):
                open_prices[sym][d] = float(adj_o)
            if adj_c is not None and not math.isnan(adj_c):
                close_prices[sym][d] = float(adj_c)

    # 4. Initialize portfolio state: 100% cash, 0 shares
    current_cash = float(initial_nav)
    current_shares: dict[str, float] = {sym: 0.0 for sym in universe}
    prev_close_nav = float(initial_nav)

    daily_nav_records: list[DailyNavRecord] = []
    position_records: list[PositionRecord] = []
    trade_records: list[TradeRecord] = []
    rebalance_records: list[RebalanceRecord] = []

    # 5. Daily simulation loop
    for s_idx, current_session in enumerate(sessions):
        # -------------------------------------------------------------
        # Step A: Check for Rebalance at Execution Open
        # -------------------------------------------------------------
        if current_session in execution_signals:
            sig = execution_signals[current_session]
            target_weights = {k: v for k, v in sig["weights"].items() if k != "CASH"}

            # On rebalance day, execution open prices are strictly required for active or target assets
            exec_opens: dict[str, float] = {}
            for sym in universe:
                p_o = open_prices[sym].get(current_session)
                if p_o is not None:
                    exec_opens[sym] = p_o

            current_shares, current_cash, new_trades, reb_record = execute_rebalance(
                signal_session=sig["signal_session"],
                execution_session=current_session,
                model_id=signals.model_id,
                current_shares=current_shares,
                current_cash=current_cash,
                execution_open_prices=exec_opens,
                target_weights=target_weights,
                cost_bps=cost_bps,
                universe=universe,
            )
            rebalance_flag = True
            day_fee = reb_record.fee
            trade_records.extend(new_trades)
            rebalance_records.append(reb_record)
        else:
            rebalance_flag = False
            day_fee = 0.0
            # Non-rebalance day: shares and cash remain strictly unchanged
            # adj_open is NOT required

        # -------------------------------------------------------------
        # Step B: Close Mark-to-Market
        # -------------------------------------------------------------
        gross_mv = 0.0
        for sym in universe:
            q = current_shares[sym]
            if q > 1e-10:
                p_c = close_prices[sym].get(current_session)
                if p_c is None or math.isnan(p_c) or p_c <= 0:
                    raise EngineError(
                        f"Session {current_session}: missing or invalid valuation close price for held asset {sym}: {p_c}"
                    )
                mv = q * p_c
                gross_mv += mv

        nav_t = current_cash + gross_mv
        if nav_t <= 0:
            raise EngineError(f"Session {current_session}: portfolio NAV wiped out: {nav_t}")

        # Record non-zero positions
        for sym in universe:
            q = current_shares[sym]
            if q > 1e-10:
                p_c = close_prices[sym][current_session]
                mv = q * p_c
                w = mv / nav_t
                position_records.append(PositionRecord(
                    date=current_session,
                    symbol=sym,
                    shares=q,
                    close=p_c,
                    market_value=mv,
                    weight=w,
                ))

        # -------------------------------------------------------------
        # Step C: Daily Return Calculation
        # -------------------------------------------------------------
        if s_idx == 0:
            # First day return directly against initial capital $100,000
            daily_return = (nav_t / initial_nav) - 1.0
        else:
            daily_return = (nav_t / prev_close_nav) - 1.0

        prev_close_nav = nav_t
        gross_exposure = gross_mv / nav_t if nav_t > 0 else 0.0

        daily_nav_records.append(DailyNavRecord(
            date=current_session,
            nav=nav_t,
            daily_return=daily_return,
            cash=current_cash,
            gross_exposure=gross_exposure,
            rebalance_flag=rebalance_flag,
            fee=day_fee,
        ))

    # 6. Convert records to DataFrames
    daily_nav_df = pd.DataFrame([vars(r) for r in daily_nav_records])
    positions_df = pd.DataFrame([vars(r) for r in position_records]) if position_records else pd.DataFrame(
        columns=["date", "symbol", "shares", "close", "market_value", "weight"]
    )
    trades_df = pd.DataFrame([vars(r) for r in trade_records]) if trade_records else pd.DataFrame(
        columns=["execution_date", "symbol", "old_shares", "new_shares", "open_price",
                 "old_notional", "new_notional", "trade_notional", "abs_trade_notional", "fee_component"]
    )
    rebalance_df = pd.DataFrame([vars(r) for r in rebalance_records]) if rebalance_records else pd.DataFrame(
        columns=["signal_session", "execution_session", "model_id", "cost_bps", "V_pre", "V_post",
                 "fee", "solver_residual", "one_way_turnover", "gross_traded_notional", "gross_traded_ratio",
                 "cash_before", "cash_after"]
    )

    pending_df = pd.DataFrame([vars(r) for r in pending_records]) if pending_records else pd.DataFrame(
        columns=["signal_session", "execution_session", "model_id", "partition", "status", "reason"]
    )

    # 7. Compute Performance Metrics
    first_session_ts = pd.Timestamp(sessions[0])
    last_session_ts = pd.Timestamp(sessions[-1])
    first_open_utc = cal.session_open(first_session_ts)
    last_close_utc = cal.session_close(last_session_ts)

    metrics = compute_backtest_metrics(
        daily_nav=daily_nav_df,
        rebalances=rebalance_df,
        initial_nav=initial_nav,
        first_session_open_utc=first_open_utc,
        last_session_close_utc=last_close_utc,
    )

    return daily_nav_df, positions_df, trades_df, rebalance_df, pending_df, metrics
