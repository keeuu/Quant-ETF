# Semantic Bridge: Research V1 vs V2 Matched Monthly

This document details the exact semantic mapping between the legacy Research V1 codebase and the Research V2 Matched Monthly comparator (`M_M12_T40`).

The Matched Monthly comparator is **not** an attempt to perfectly replicate V1. It is a strict cadence comparator designed to evaluate the isolated effect of execution frequency while holding the weekly feature logic (`W_M12_T40`) constant. 

Crucially, **V1 vs V2 Matched Monthly cannot be used to estimate pure cadence effect** because their underlying feature definitions (momentum lookback/skip windows, trend moving average frequency) are different. The pure cadence effect is strictly evaluated by comparing:
$$\text{W\_M12\_T40 (Weekly Cadence)} \quad \text{vs} \quad \text{M\_M12\_T40 (Matched Monthly Cadence)}$$

## 1. Cadence & Rebalance

| Feature | Research V1 | Research V2 Matched Monthly (`M_M12_T40`) |
| :--- | :--- | :--- |
| **Decision Timing** | Month-end close | Month-end close |
| **Execution Timing** | First valid trading session of next month (at `adj_open`) | First valid trading session of next month (at `adj_open`) |
| **Signal Source** | Computed ad-hoc on month-end closes. | Sampled exactly from the latest *completed* weekly signal (`W_M12_T40`) as of month-end close. No partial mid-week data is computed. |

## 2. Momentum

| Feature | Research V1 (12-1 Momentum) | Research V2 Matched Monthly (`M_M12_T40`) |
| :--- | :--- | :--- |
| **Lookback Convention** | 12 months (via `monthly_prices.shift(12)`) | 12 natural months anchor |
| **Skip Convention** | 1 month (via `monthly_prices.shift(1)`) | 1 completed weekly session |

## 3. Trend Filter

| Feature | Research V1 (10-Month SMA) | Research V2 Matched Monthly (`M_M12_T40`) |
| :--- | :--- | :--- |
| **Simple Moving Average** | 10-Month SMA computed strictly on monthly closing prices | 40-Week SMA computed strictly on weekly closing prices |

## 4. Ledger & Accounting

| Feature | Research V1 | Research V2 |
| :--- | :--- | :--- |
| **Transaction Costs** | Explicit 5 bps | Explicit self-financing solver (5 bps) |
| **Accounting Standard** | Custom iterative array tracking | Strict immutable event-sourced ledger (`engine.py`) |
