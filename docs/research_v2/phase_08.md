# Research V2 — Stage 8

## Objective
This stage establishes the pre-registered robustness bounds and cost sensitivities of the 9 weekly models and their 9 matched monthly cadence controls, prior to unlocking the Holdout partition. The primary outputs include cost monotonicity verification, stationary bootstrap sampling uncertainties, and SPY-conditional regime performance.

## Scope and Guardrails
1. **Model Selection:** `MODEL_SELECTION = NONE`. Stage 8 outputs descriptive robustness boundaries for the pre-registered candidate pool.
2. **Parameters:** Zero new hyperparameters or trend/momentum values were searched.
3. **Partition:** Pre-Holdout Pooled interval (`2006-02-01` to `2020-12-31`).
4. **Holdout Firewall:** Verified intact. The retrospective holdout partition remains unread and fails closed.

## Protocol Audit and Analysis Lock
Original Stage 1 Frozen Definitions Used: **YES**
The moving-block stationary bootstrap and SPY SMA200 regime definitions were fully inherited from the initial `protocol_lock.json`. These specifications were deterministically applied without modification.

## Cost Monotonicity
- Grid: 0, 5, 10, 20 bps
- Models evaluated: 9 Weekly + 9 Matched Monthly
- Verification: **PASS**. Monotonicity invariant (higher transaction costs monotonically reduce Final NAV for an identical rebalance path) holds cleanly across all models and cadences.

## Cadence Evaluation
Cadence paired comparisons were evaluated identically at the baseline cost of 5 bps. No interpolation was used to optimize the "break-even" transaction cost spread.

## Bootstrap Uncertainty
- Method: Paired Stationary Block Bootstrap
- Block Length: 20 trading sessions (geometric expectation)
- Resamples: 10,000
- Seed: 20260921 (deterministic)
- Usage: `BOOTSTRAP_USED_FOR_UNCERTAINTY_ONLY`. Multiple testing bounds generated here map exclusively to estimating uncertainty, preventing naive optimization via selection.

## Regime Definition
- Rule: `SPY` Adj Close vs `SPY` 200-Day Simple Moving Average (SMA)
- Evaluation: Calculated deterministically with a 1-day lag to strictly avoid lookahead bias.
- Coverage: Broad `BULL` (SPY > SMA200) and `BEAR` (SPY <= SMA200) labels mapped to concurrent returns. NO small sample size warning issued (samples well above 126 minimum sessions).

## Structural Preservation
The finding from Stage 7 that Train-to-Validation period rank preservation was weak remains fundamentally intact. Parameter rank stability across unseen intervals relies heavily upon robust macroeconomic continuity.

## Holdout Lock
The Stage 9 Holdout Lock has been crystallized at `docs/research_v2/stage_09_holdout_lock.json`. 
- **Package:** 9 Weekly Models + 9 Monthly Cadence Controls.
- **Cost:** Fixed at 5 bps.
- **Rule:** `DESCRIPTIVE_HOLDOUT / NO_PROMOTION_DECISION`.
- **Atomic Requirement:** Holdout must be computed synchronously as one indivisible package to prevent step-wise exploratory tuning.

## Stage 8B Holdout Seal Hardening
Following independent audit, the initial Holdout Lock was rejected (`REJECTED_PRE_EXPOSURE`) due to incomplete cryptographic bindings. 

A strictly hardened seal `stage_09_holdout_lock_v2.json` was generated locking:
- `dataset_sha256`: Verified source dataset identity.
- `protocol_sha256`: Verified frozen execution protocol.
- `execution_commit` / `execution_tree_sha`: Exact code path.
- Holdout Partition is now strictly labeled as `RETROSPECTIVE_HOLDOUT` subject to `NOT_TRUE_FINAL_UNSEEN` limitation.

The Stage 9 execution runner interface and tests have been implemented but not executed. A narrow authorization object `Stage9HoldoutAuthorization` acts as the exclusive lock-and-key gate to allow loading retrospective holdout market data. State Machine ensures any exposed data permanently invalidates future retries.
