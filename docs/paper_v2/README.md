# Paper V2: Prospective Paper Trading Layer

## Architecture
Paper V2 strictly builds on top of the `research_v2` read-only primitives. 
- It uses the canonical API functions like `compute_model_signals_for_events` for generating signals.
- It uses `execute_rebalance` for self-financing portfolio accounting.
- It maintains its own local state in `paper_v2_state/`.

## Activation Protocol
Paper V2 tracks `activation_timestamp`, `activation_git_commit`, and `activation_config_hash`.
No historical backfilling is allowed. Only strictly prospective signals that occur after activation can be computed and executed.

## Model Package
18 independent portfolios (9 Weekly, 9 Matched Monthly). Each has its own independent cash, positions, and NAV.

## Execution Timing
Two phases:
1. Signal Phase: runs after market close of complete week. Generates pending target allocation.
2. Execution Phase: runs on the next XNYS open session. Executes target allocations using the open prices.

## Journal Schema
- `signal_journal.jsonl`: Append-only records of computed signals, weights, and source data hashes.
- `execution_journal.jsonl`: Append-only records of trade executions, cash/share flows, and fees.
- `daily_nav.jsonl`: Append-only daily marks for NAV, cash, and market value.

## Restart Semantics
Uses `state.json` for persistence of 18 portfolios and pending orders. 
Restarting the CLI does not lose pending orders. Execution and signal phases are idempotent.

## Failure Semantics
Fail-closed on any missing data, mismatched calendar, or ledger invariant failures.

## No-Real-Money Guarantee
Paper V2 operates in `LOCAL_SHADOW_ONLY` mode. Broker imports (`alpaca`, `ibkr`) are strictly prohibited and regression tested.

## Prospective OOS Rule
Requires >= 504 sessions and >= 24 months to transition to `PROSPECTIVE_FINAL_OOS_COMPLETE`. OOS counting only increments for strictly prospective days post-activation.
