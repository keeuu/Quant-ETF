# Research V2 — Stage 4 Implementation Plan: Self-Financing Ledger, Daily MTM, Transaction Costs & Metrics

> Document Path: `docs/research_v2/phase_04_implementation_plan.md`
> Created: 2026-09-23
> Scope: Self-Financing Portfolio Accounting, Daily MTM Engine, Metrics, Synthetic Tests, and Train-only Canary.

---

## 1. Scope & Frozen Boundaries

- **Frozen Code & Data**: Research V1 (`research-v1`), Paper V1 (`paper-v1-ready`), Stage 3 Git Checkpoint (`research-v2-stage3-ready`).
- **Frozen Strategy Protocols**: Signals, momentum, trend, selection, DST logic, calendar, and split definitions remain untouched.
- **Strict Access Gating**:
  - Validation: NOT ACCESSED
  - Retrospective Holdout: NOT ACCESSED
  - Prospective Final OOS: NOT ACCESSED
- **Stage 4 Target**: Self-financing accounting, daily MTM, transaction cost handling, turnover dual metrics, daily performance metrics, synthetic test suite, and one single Train canary (`W_M12_T40`, 5 bps, \$100,000 initial cash). No model comparisons, no tuning.

---

## 2. Core Mathematical Specifications

### 2.1 Self-Financing Equilibrium Equation

On any rebalance trading session $t$ at execution open:
- Old asset shares: $q^{old}_i$
- Execution open price: $p^{open}_i$ (using Stage 2 frozen `adj_open`)
- Pre-trade asset notional: $a_i = q^{old}_i \cdot p^{open}_i$
- Pre-trade cash: $cash^{old}$
- Pre-trade wealth: $V_{pre} = cash^{old} + \sum_i a_i$
- Target risky weights: $w_i \ge 0, \sum_i w_i \le 1$
- Target cash weight: $w_{cash} = 1 - \sum_i w_i \ge 0$
- Cost rate: $c = \frac{cost\_bps}{10000}$
  - **Domain Constraint**: $0 \le c < 1$. If $c < 0$ or $c \ge 1$ or NaN/inf: FAIL CLOSED.
- Target asset dollar position: $x_i = w_i \cdot V_{post}$
- Risky asset trade notional: $trade_i = x_i - a_i = w_i \cdot V_{post} - a_i$
- Transaction fee (cash itself is exempt from fee):
  $$fee(V_{post}) = c \sum_i |w_i \cdot V_{post} - a_i|$$
- Self-financing equilibrium equation:
  $$F(V_{post}) \equiv V_{post} + c \sum_i |w_i \cdot V_{post} - a_i| - V_{pre} = 0$$

### 2.2 Piecewise-Linear Root Solver
- Since $0 \le c < 1$ and $\sum w_i \le 1$, $F'(V) \ge 1 - c > 0$, so $F(V)$ is strictly monotonically increasing and continuous.
- Bracketing on $[0, V_{pre}]$:
  $$F(0) \le 0, \quad F(V_{pre}) \ge 0$$
- Solver uses deterministic bounded Brent / Bisection with absolute tolerance:
  $$|F(V_{post})| \le 10^{-10} \times \max(1, V_{pre})$$
- Post-trade updates:
  - $q^{new}_i = \frac{w_i \cdot V_{post}}{p^{open}_i}$
  - $cash^{new} = w_{cash} \cdot V_{post}$
- Invariant assertions:
  - $V_{post} = V_{pre} - fee$
  - $cash^{new} + \sum_i (q^{new}_i \cdot p^{open}_i) = V_{post}$
  - $cash^{new} \ge -10^{-8}$ (real negative cash fails closed; no artificial clamping)
  - $q^{new}_i \ge -10^{-8}$ (no shorting)

---

## 3. Daily Temporal Accounting & Price Dependencies

1. **Non-Rebalance Trading Day**:
   - `adj_open` is **NOT** required.
   - Holdings remain constant: $q_{t, i} = q_{t-1, i}$.
   - Cash remains constant: $cash_t = cash_{t-1}$.
   - Close valuation: $NAV_t = cash_t + \sum_i (q_{t, i} \cdot adj\_close_{t, i})$.
   - If `adj_close` is missing: FAIL CLOSED. If non-rebalance `adj_open` is missing, ledger continues safely.

2. **Rebalance Trading Day (Execution Session)**:
   - `adj_open` is **REQUIRED**; if missing: FAIL CLOSED.
   - Step 1: Yesterday close holdings held across overnight gap into execution open:
     $$V_{pre} = cash_{t-1} + \sum_i (q_{t-1, i} \cdot adj\_open_{t, i})$$
   - Step 2: Solve self-financing root for $V_{post}$ and $fee$.
   - Step 3: Rebalance into new holdings $q_{t, i}$ and new cash $cash_t$.
   - Step 4: Hold new positions intraday until today's close:
     $$NAV_t = cash_t + \sum_i (q_{t, i} \cdot adj\_close_{t, i})$$

3. **First-Day Return**:
   - Initial portfolio: \$100,000 in 100% cash.
   - Session 1 return: $daily\_return_1 = \frac{NAV_1}{100000} - 1$.
   - If initial trades occur on session 1, transaction costs make $daily\_return_1 < 0$.
   - Subsequent daily returns: $daily\_return_t = \frac{NAV_t}{NAV_{t-1}} - 1$. Never drop first-day return.

---

## 4. Turnover & Performance Metrics

### 4.1 Dual Turnover Metrics
1. **Cash-Inclusive Target One-Way Turnover**:
   $$turnover\_one\_way_j = 0.5 \sum_{k \in \{8 \text{ ETFs}\} \cup \{\text{CASH}\}} |w^{target}_{k,j} - w^{pretrade}_{k,j}|$$
   Annualized: $\sum_j turnover\_one\_way_j / elapsed\_years$.
2. **Gross Traded Notional Ratio**:
   $$gross\_traded\_notional_j = \sum_{i \in \{8 \text{ ETFs}\}} |trade\_notional_{i,j}|$$
   $$gross\_trade\_ratio_j = \frac{gross\_traded\_notional_j}{V_{pre,j}}$$
   Annualized: $\sum_j gross\_trade\_ratio_j / elapsed\_years$.

### 4.2 Performance Metrics Specifications
- **Elapsed Time**:
  Calculated using timezone-aware UTC timestamps from first scored session open to last scored session close:
  $$elapsed\_years = \frac{(last\_session\_close\_utc - first\_session\_open\_utc).\text{total\_seconds}()}{365.25 \times 86400}$$
  Strictly avoids integer `.days` truncation or `trading_days / 252`.
- **CAGR**:
  $$CAGR = \left(\frac{NAV_{final}}{100000}\right)^{1 / elapsed\_years} - 1$$
- **Daily Annualized Volatility**:
  $$Vol = \text{std}(daily\_returns, ddof=1) \times \sqrt{252}$$
  If $std == 0$ or samples $< 2$, returns `None`.
- **Daily Sharpe Ratio**:
  $$Sharpe = \frac{\text{mean}(daily\_returns)}{\text{std}(daily\_returns, ddof=1)} \times \sqrt{252}$$
  If $std == 0$, returns `None` (never `inf` or `0`).
- **Daily Max Drawdown**:
  Peak-to-trough drop evaluated across daily NAV series:
  $$MaxDD = \max_t \left( \frac{peak_t - NAV_t}{peak_t} \right)$$
  Reported as positive loss magnitude.

---

## 5. Modules to Create & Modify

1. `research_v2/ledger.py` [NEW]:
   - `SelfFinancingSolver` with domain check $0 \le c < 1$.
   - `execute_rebalance` with full invariant audits.
2. `research_v2/engine.py` [NEW]:
   - `run_backtest` daily session loop.
   - Non-rebalance open decoupling.
   - Multi-table output generator (`daily_nav`, `positions`, `trades`, `rebalance`).
3. `research_v2/metrics.py` [NEW]:
   - `compute_backtest_metrics` adhering to exact protocol conventions.
4. `research_v2/contracts.py` [MODIFY]:
   - Stage 4 record definitions.
5. `research_v2/cli.py` [MODIFY]:
   - Subcommands `canary` and `audit-ledger`.
6. Test suite in `tests_v2/` [NEW]:
   - `test_ledger.py`: Tests A–F, solver bounds, residual tolerance, negative cash fail-closed.
   - `test_daily_mtm.py`: Timing tests (overnight gap, intraday crash, first-day cost), open-price dependency decoupling.
   - `test_costs_turnover.py`: Multi-cost monotonicity, turnover dual definition divergence.
   - `test_metrics.py`: CAGR total-seconds precision, zero-vol Sharpe NA, known path MaxDD.
   - `test_engine.py`: Partition isolation (\$100,000 cash start), terminal schedule handling.
