# Research V2 — Stage 7

## Objective
This stage executes Validation and 10-fold annual Walk-Forward evaluations across the pre-registered 3x3 weekly parameter grid (`W_M6_T36` through `W_M12_T44`), computes parameter surface stability, and executes convergence and lookahead/leakage audits.

## Scope and Guardrails
1. **Model Universe Frozen:** Exactly 9 pre-registered models. Zero parameter expansions, zero post-hoc models.
2. **Partition Gating:**
   - Train: `2006-02-01` to `2015-12-31` (frozen baseline).
   - Validation: `2016-01-01` to `2020-12-31` (accessed as authorized).
   - Retrospective Holdout (`2021-01-01` to `2026-09-18`): strictly NOT_ACCESSED.
   - Prospective OOS: PENDING_DATA.
3. **No Model Selection:** `MODEL_SELECTION = NONE`.
   - Descriptive stability analysis only.
   - No best, winner, optimal, recommended, or promoted model designations.
4. **Cadence Consistency:** All models share the identical weekly cadence; differences are attributable strictly to different trend-window / momentum-window parameter combinations.

## Boundary Accounting
- Initial Wealth: 100,000.00 USD
- Initial Portfolio: 100% CASH (no wealth or position carryover from Train)
- Carry-In Decision: 1 (Decision from `2015-12-31`, executed on `2016-01-04` open)
- In-Segment Decisions Executed: 260
- Total Rebalances Executed: 261
- Terminal Pending: 1 (`2020-12-31` decision deferred outside Validation segment as `PENDING_OUTSIDE_SEGMENT`)

## Parameter Stability & Rank Correlation
Train-to-Validation Spearman rank correlations:
- CAGR: -0.233333
- Sharpe: -0.400000
- Max Drawdown: -0.340503
- One-Way Turnover: 0.266667

### Interpretation Lock
- `within-period local smoothness does not imply cross-period rank stability`
- While within-period parameter surfaces exhibit local continuity, relative performance ordering across different trend-window and momentum-window parameter combinations inverted moderately between Train and Validation periods.
- In accordance with the pre-registered protocol, no parameter selection or promotion is conducted.

## All-Cash Fallback Events
In the Validation period, the frozen allocation rule (0 qualified risky assets passing trend filter leads to 100% cash allocation) was exercised during the market downturn of late 2018:
- **T36 and T40 models** (`W_M6_T36`, `W_M6_T40`, `W_M9_T36`, `W_M9_T40`, `W_M12_T36`, `W_M12_T40`):
  - Exercised all-cash fallback for 5 trading sessions (`2018-11-05` to `2018-11-09`).
  - `mean_cash_weight` ≈ 0.003971 (5 / 1259 days).
  - `max_cash_weight` = 1.000000.
- **T44 models** (`W_M6_T44`, `W_M9_T44`, `W_M12_T44`):
  - Exercised all-cash fallback for 10 trading sessions (`2018-10-29` to `2018-11-09`).
  - `mean_cash_weight` ≈ 0.007943 (10 / 1259 days).
  - `max_cash_weight` = 1.000000.

Note: The all-cash fallback mechanism was exercised as specified by the protocol rules.

## Mean Cash Provenance Correction
- `ARTIFACT_CORRECTION_REQUIRED`: Initial Stage 7 textual summary reported Mean Cash as 0.000000 due to omitting `mean_cash_weight` in the initial `validation_trial_ledger.csv` generation script.
- The values have been dynamically derived from `daily_nav.csv` cash paths and written to `validation_trial_ledger.csv` and `validation_surface_cash.csv`.

## Yearly Walk-Forward
- 10 chronological annual folds (`WF_2011` through `WF_2020`).
- Each fold starts from 100,000 cash with independent fold evaluation and no cross-fold PnL carryover.
- All 9 models evaluated across all 10 folds.

## Audits
- **Convergence Audit:** `PREFIX_INVARIANCE_PASS`.
- **Lookahead / Leakage Audit:** `future_poisoning = PASS`, `execution_open_isolation = PASS`, `same_day_ordering = PASS`.
- **Holdout Firewall:** `PASS` (fails closed when holdout partition requested).
