# Research V2 — Stage 6

## Objective
This stage establishes the pre-registered 3x3 Weekly candidate grid (`W_M6_T36` to `W_M12_T44`). The goal is exclusively to perform a **Train-only descriptive parameter-surface analysis** in order to analyze neighborhood stability and parameter dispersion.

## Scope and Guardrails
1. **No Model Selection:** This stage explicitly prohibits model selection. There is no "best", "winner", "optimal", or "recommended" model identified here.
2. **Train Only:** All executions are restricted to the Train partition (2006-02-01 to 2015-12-31). Validation (2016-2020) and Holdout (2021+) data are strictly unaccessed and protected.
3. **No Metric Aggregation:** Stability and performance metrics are provided exactly as computed without generating any arbitrary composite score. Ranks, if recorded, are strictly descriptive.

## Metrics Extracted
For each point in the 3x3 parameter surface, the following descriptive metrics are evaluated:
- CAGR, Daily Volatility, Daily Sharpe, Daily Max Drawdown
- Annualized One-Way Turnover, Annualized Gross Traded Ratio
- Mean Cash Weight, Total Fees
