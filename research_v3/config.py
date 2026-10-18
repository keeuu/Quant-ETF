"""Run parameters for research_v3 (step 1: measurement fixes only; signals untouched)."""
from __future__ import annotations

from dataclasses import dataclass

CASH_YIELD_SOURCES = ("none", "irx")


@dataclass(frozen=True)
class V3Config:
    """cost_bps: v2 commission-like cost per side on traded notional.
    slippage_bps: additional per-side cost on traded notional. Implemented as
        effective_cost_bps = cost_bps + slippage_bps passed to the v2 self-financing ledger.
    cash_yield_source: "none" (cash earns 0%, v2-equivalent) or "irx" (13-week T-bill, ^IRX).
    """
    cost_bps: float = 5.0
    slippage_bps: float = 5.0
    cash_yield_source: str = "irx"
    initial_nav: float = 100000.0

    def __post_init__(self) -> None:
        if self.cash_yield_source not in CASH_YIELD_SOURCES:
            raise ValueError(f"cash_yield_source must be one of {CASH_YIELD_SOURCES}, got {self.cash_yield_source!r}")
        if self.cost_bps < 0 or self.slippage_bps < 0:
            raise ValueError("cost_bps and slippage_bps must be >= 0")

    @property
    def effective_cost_bps(self) -> float:
        return self.cost_bps + self.slippage_bps


V2_EQUIVALENT = V3Config(cost_bps=5.0, slippage_bps=0.0, cash_yield_source="none")
V3_DEFAULT = V3Config()
