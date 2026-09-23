"""Self-financing portfolio ledger and piecewise-linear root solver for Research V2.

Strictly solves:
    V_post + c * sum(|w_i * V_post - a_i|) = V_pre
Guarantees:
- Residual <= 1e-10 * max(1, V_pre)
- Exact portfolio conservation: V_post = V_pre - fee
- Fail-closed: no artificial cash clamping, no negative shares, 0 <= c < 1
- Independent tracking of cash-inclusive one-way turnover and gross traded notional ratio
"""
from __future__ import annotations

import math
from typing import Any, Mapping, Sequence

from .contracts import RebalanceRecord, TradeRecord, UNIVERSE


class LedgerError(ValueError):
    """Raised when ledger invariants or input sanity checks fail."""
    pass


class LedgerSolverError(LedgerError):
    """Raised when the self-financing root solver fails to converge within tolerance."""
    pass


def brentq_solve(
    f: Any,
    a: float,
    b: float,
    xtol: float = 1e-14,
    rtol: float = 1e-14,
    maxiter: int = 100,
) -> float:
    """Deterministic, robust Brent's method root solver with strict bracket verification.

    Independent of external C-library differences and fail-closed on bracket violations.
    """
    fa = f(a)
    fb = f(b)

    if math.isnan(fa) or math.isnan(fb):
        raise LedgerSolverError("NaN encountered in objective function")

    if abs(fa) <= xtol:
        return a
    if abs(fb) <= xtol:
        return b

    if fa * fb > 0:
        raise LedgerSolverError(f"Root is not bracketed: f({a})={fa}, f({b})={fb}")

    if abs(fa) < abs(fb):
        a, b = b, a
        fa, fb = fb, fa

    c = a
    fc = fa
    mflag = True
    d = 0.0

    for _ in range(maxiter):
        if abs(fb) <= xtol:
            return b

        if abs(b - a) <= (xtol + rtol * abs(b)):
            return b

        if fa != fc and fb != fc:
            # Inverse quadratic interpolation
            s = (a * fb * fc / ((fa - fb) * (fa - fc)) +
                 b * fa * fc / ((fb - fa) * (fb - fc)) +
                 c * fa * fb / ((fc - fa) * (fc - fb)))
        else:
            # Secant method
            s = b - fb * (b - a) / (fb - fa)

        # Conditions for bisection fallback
        cond1 = not ((3 * a + b) / 4 <= s <= b or b <= s <= (3 * a + b) / 4)
        cond2 = mflag and abs(s - b) >= abs(b - c) / 2
        cond3 = not mflag and abs(s - b) >= abs(c - d) / 2
        cond4 = mflag and abs(b - c) < xtol
        cond5 = not mflag and abs(c - d) < xtol

        if cond1 or cond2 or cond3 or cond4 or cond5:
            s = (a + b) / 2
            mflag = True
        else:
            mflag = False

        fs = f(s)
        if math.isnan(fs):
            raise LedgerSolverError("NaN encountered during root iteration")

        d = c
        c = b
        fc = fb

        if fa * fs < 0:
            b = s
            fb = fs
        else:
            a = s
            fa = fs

        if abs(fa) < abs(fb):
            a, b = b, a
            fa, fb = fb, fa

    return b


class SelfFinancingSolver:
    """Solves the piecewise-linear self-financing equilibrium equation:

    F(V) = V + c * sum(|w_i * V - a_i|) - V_pre = 0

    where:
    - V_pre is pre-trade wealth: cash_old + sum(a_i)
    - a_i is pre-trade dollar value of risky asset i: q_old_i * p_open_i
    - w_i is target weight of risky asset i: sum(w_i) <= 1
    - c is cost rate: cost_bps / 10000, strictly in [0, 1)
    """

    @staticmethod
    def solve(
        V_pre: float,
        a: Mapping[str, float],
        w: Mapping[str, float],
        c: float,
        tolerance_factor: float = 1e-10,
    ) -> tuple[float, float, float]:
        """Solve for (V_post, fee, residual).

        Returns:
            V_post: post-trade portfolio wealth
            fee: total transaction fee incurred
            residual: absolute residual |F(V_post)|
        """
        # 1. Cost domain check (strictly 0 <= c < 1)
        if math.isnan(c) or math.isinf(c) or c < 0.0 or c >= 1.0:
            raise LedgerError(f"Cost rate c must be in [0, 1), got {c}")

        # 2. Pre-trade wealth sanity
        if math.isnan(V_pre) or math.isinf(V_pre) or V_pre < -1e-10:
            raise LedgerError(f"Invalid pre-trade wealth V_pre: {V_pre}")

        tol = tolerance_factor * max(1.0, V_pre)

        # Zero wealth edge case
        if V_pre <= 0.0:
            return 0.0, 0.0, 0.0

        # Zero cost edge case
        if c == 0.0:
            return V_pre, 0.0, 0.0

        # Check if pre-trade weights already exactly match target weights
        diff_sum = sum(abs(w.get(sym, 0.0) * V_pre - a.get(sym, 0.0)) for sym in set(a) | set(w))
        if diff_sum <= 1e-12:
            return V_pre, 0.0, 0.0

        symbols = sorted(set(a) | set(w))
        a_vec = [float(a.get(s, 0.0)) for s in symbols]
        w_vec = [float(w.get(s, 0.0)) for s in symbols]

        def F(V: float) -> float:
            fee_val = c * sum(abs(w_i * V - a_i) for w_i, a_i in zip(w_vec, a_vec))
            return V + fee_val - V_pre

        # Bracket is strictly [0, V_pre]
        f_0 = F(0.0)
        f_vpre = F(V_pre)

        if f_0 > tol:
            raise LedgerSolverError(f"Left bracket violation: F(0) = {f_0} > {tol}")
        if f_vpre < -tol:
            raise LedgerSolverError(f"Right bracket violation: F(V_pre) = {f_vpre} < -{tol}")

        if abs(f_0) <= tol:
            V_post = 0.0
        elif abs(f_vpre) <= tol:
            V_post = V_pre
        else:
            V_post = brentq_solve(F, 0.0, V_pre, xtol=1e-15, rtol=1e-15, maxiter=100)

        # Final verification
        final_fee = c * sum(abs(w_i * V_post - a_i) for w_i, a_i in zip(w_vec, a_vec))
        residual = abs(V_post + final_fee - V_pre)

        if residual > tol:
            raise LedgerSolverError(
                f"Self-financing residual {residual:.2e} exceeds tolerance {tol:.2e} (V_pre={V_pre}, V_post={V_post})"
            )

        # Post wealth must not exceed pre-trade wealth plus micro numerical noise
        if V_post > V_pre + tol:
            raise LedgerSolverError(f"V_post {V_post} exceeds V_pre {V_pre}")

        return V_post, final_fee, residual


def execute_rebalance(
    signal_session: str,
    execution_session: str,
    model_id: str,
    current_shares: Mapping[str, float],
    current_cash: float,
    execution_open_prices: Mapping[str, float],
    target_weights: Mapping[str, float],
    cost_bps: float,
    universe: Sequence[str] = UNIVERSE,
) -> tuple[dict[str, float], float, list[TradeRecord], RebalanceRecord]:
    """Execute target allocation rebalance at execution open with exact self-financing accounting.

    Returns:
        (new_shares, new_cash, trades, rebalance_record)
    """
    c = cost_bps / 10000.0

    # 1. Validate target weights
    total_risky_w = 0.0
    for sym, w in target_weights.items():
        if math.isnan(w) or math.isinf(w) or w < -1e-10:
            raise LedgerError(f"Target weight for {sym} must be non-negative, got {w}")
        total_risky_w += w

    if total_risky_w > 1.0 + 1e-8:
        raise LedgerError(f"Total risky target weights {total_risky_w} exceed 1.0")

    w_cash = max(0.0, 1.0 - total_risky_w)

    # 2. Check prices and compute pre-trade asset values a_i
    a: dict[str, float] = {}
    for sym in universe:
        old_q = current_shares.get(sym, 0.0)
        target_w = target_weights.get(sym, 0.0)

        # Price is strictly required if we currently hold shares or target to hold shares
        if old_q > 1e-10 or target_w > 1e-10:
            p_open = execution_open_prices.get(sym)
            if p_open is None or math.isnan(p_open) or math.isinf(p_open) or p_open <= 0.0:
                raise LedgerError(
                    f"Execution session {execution_session}: missing or non-positive execution open price for {sym}: {p_open}"
                )
            a[sym] = old_q * p_open
        else:
            a[sym] = 0.0

    # 3. Pre-trade wealth
    if math.isnan(current_cash) or math.isinf(current_cash):
        raise LedgerError(f"Invalid current cash: {current_cash}")

    V_pre = current_cash + sum(a.values())
    if V_pre < -1e-8:
        raise LedgerError(f"Negative pre-trade wealth V_pre: {V_pre}")

    # 4. Solve self-financing root
    V_post, fee, residual = SelfFinancingSolver.solve(
        V_pre=V_pre,
        a=a,
        w=target_weights,
        c=c,
    )

    # 5. Compute new cash and share positions
    new_cash = w_cash * V_post
    if new_cash < -1e-8:
        raise LedgerError(f"Substantial negative post-trade cash: {new_cash}")
    # Bound micro floating-point rounding
    new_cash = max(0.0, new_cash)

    new_shares: dict[str, float] = {}
    trades: list[TradeRecord] = []
    gross_traded_notional = 0.0

    for sym in universe:
        old_q = current_shares.get(sym, 0.0)
        target_w = target_weights.get(sym, 0.0)
        old_notional = a[sym]
        new_notional = target_w * V_post

        if target_w > 1e-10 or old_q > 1e-10:
            p_open = execution_open_prices[sym]
            new_q = new_notional / p_open
        else:
            p_open = execution_open_prices.get(sym, 0.0)
            new_q = 0.0

        if new_q < -1e-8:
            raise LedgerError(f"Negative share quantity calculated for {sym}: {new_q}")
        new_q = max(0.0, new_q)
        new_shares[sym] = new_q

        trade_notional = new_notional - old_notional
        abs_trade_notional = abs(trade_notional)
        gross_traded_notional += abs_trade_notional
        fee_component = c * abs_trade_notional

        # Record trade if there is actual non-trivial turnover
        if abs_trade_notional > 1e-6 or abs(new_q - old_q) > 1e-6:
            trades.append(TradeRecord(
                execution_date=execution_session,
                symbol=sym,
                old_shares=old_q,
                new_shares=new_q,
                open_price=p_open,
                old_notional=old_notional,
                new_notional=new_notional,
                trade_notional=trade_notional,
                abs_trade_notional=abs_trade_notional,
                fee_component=fee_component,
            ))

    # 6. Turnover calculations
    gross_traded_ratio = gross_traded_notional / V_pre if V_pre > 0.0 else 0.0

    # Cash-inclusive pre-trade weights
    w_pretrade_cash = current_cash / V_pre if V_pre > 0.0 else 1.0
    w_pretrade_risky = {sym: a[sym] / V_pre if V_pre > 0.0 else 0.0 for sym in universe}

    turnover_components = abs(w_cash - w_pretrade_cash) + sum(
        abs(target_weights.get(sym, 0.0) - w_pretrade_risky[sym]) for sym in universe
    )
    one_way_turnover = 0.5 * turnover_components

    # 7. Strict Conservation Invariants
    post_valuation_at_open = new_cash + sum(
        new_shares[sym] * execution_open_prices[sym] for sym in universe if new_shares[sym] > 0
    )
    tol = 1e-8 * max(1.0, V_pre)

    if abs(post_valuation_at_open - V_post) > tol:
        raise LedgerError(
            f"Open post-trade valuation mismatch: {post_valuation_at_open} vs {V_post} (diff={post_valuation_at_open - V_post})"
        )

    if abs(V_post - (V_pre - fee)) > tol:
        raise LedgerError(f"Fee wealth conservation mismatch: V_post={V_post}, V_pre={V_pre}, fee={fee}")

    rebalance_record = RebalanceRecord(
        signal_session=signal_session,
        execution_session=execution_session,
        model_id=model_id,
        cost_bps=cost_bps,
        V_pre=V_pre,
        V_post=V_post,
        fee=fee,
        solver_residual=residual,
        one_way_turnover=one_way_turnover,
        gross_traded_notional=gross_traded_notional,
        gross_traded_ratio=gross_traded_ratio,
        cash_before=current_cash,
        cash_after=new_cash,
    )

    return new_shares, new_cash, trades, rebalance_record
