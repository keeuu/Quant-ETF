"""Stage-1 contracts only. No signal computation, trading, or data loading."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol, Sequence

from .provenance import object_hash

EXPECTED_PROTOCOL_SHA256 = "a35fe1e50f90b537bd543e9d41a78eff3d9ae9991c362adc603f42432a23e9fb"
EXPECTED_PLAN_SHA256 = "5d8dc33318214cd0493ad320ecc122e46b057ab74b9c1b54a80a0b1f0b9cbeea"
UNIVERSE = ("SPY", "QQQ", "IWM", "EFA", "EEM", "GLD", "TLT", "IEF")


def validate_protocol(protocol: Mapping[str, Any]) -> str:
    """Validate the exact reviewed registration, including all nested policies.

    This digest is an implementation anchor, not a signature. Amendments require
    a new experiment and lock, never editing these existing frozen artifacts.
    """
    if protocol.get("universe") != list(UNIVERSE):
        raise ValueError("Universe/order differs from contract")
    grid = protocol.get("model_grid", {})
    expected = [{"model_id": f"W_M{m:02d}_T{t}", "momentum_months": m, "trend_weeks": t}
                for m in (6, 9, 12) for t in (36, 40, 44)]
    if grid.get("models") != expected:
        raise ValueError("Model grid must be the fixed nine unique models")
    portfolio = protocol.get("portfolio", {})
    if (portfolio.get("top_n") != 3 or portfolio.get("leverage_allowed") is not False
            or portfolio.get("short_allowed") is not False or portfolio.get("long_only") is not True):
        raise ValueError("Long-only unlevered Top 3 constraint changed")
    digest = object_hash(protocol)
    if digest != EXPECTED_PROTOCOL_SHA256:
        raise ValueError("Protocol differs from the reviewed execution contract")
    return digest


@dataclass(frozen=True)
class DatasetManifest:
    dataset_id: str
    data_sha256: str
    calendar_sha256: str
    protocol_sha256: str
    partitions: Mapping[str, Mapping[str, str]]


@dataclass(frozen=True)
class DatasetView:
    manifest: DatasetManifest
    partition: str
    as_of: str
    feature_start: str
    score_start: str
    score_end: str
    bars: Any


@dataclass(frozen=True)
class PeriodEvent:
    period_id: str
    signal_session: str
    signal_close_ts: str
    execution_session: str
    execution_open_ts: str
    feature_cutoff: str
    status: str
    source_signal_session: str | None = None


@dataclass(frozen=True)
class PeriodTable:
    rows: Sequence[Mapping[str, Any]]


@dataclass(frozen=True)
class SignalFrame:
    model_id: str
    rows: Any
    lineage: Any


@dataclass(frozen=True)
class RunSpec:
    model_id: str
    role: str
    partition: str
    cost_bps: float
    seed: int
    protocol_sha256: str
    data_sha256: str
    code_commit: str
    code_sha256: str
    calendar_sha256: str


@dataclass(frozen=True)
class RunResult:
    spec: RunSpec
    daily_nav: Any
    trades: Any
    metrics: Mapping[str, Any]


# Typing interfaces, deliberately not fake implementations of later stages.
class ResearchInterfaces(Protocol):
    def load_dataset(self, manifest_path: str, partition: str, as_of: str) -> DatasetView: ...
    def build_periods(self, sessions: Any, bars: Any, as_of: str) -> PeriodTable: ...
    def compute_signals(self, weekly_close: Any, daily_close: Any, model_spec: Mapping) -> SignalFrame: ...
    def build_schedule(self, periods: PeriodTable, cadence: str, as_of: str) -> list[PeriodEvent]: ...
    def simulate(self, dataset_view: DatasetView, targets: Any, schedule: Sequence[PeriodEvent], run_spec: RunSpec) -> RunResult: ...
    def evaluate(self, daily_nav: Any, trades: Any, initial_nav: float, interval: tuple[str, str]) -> Mapping: ...


@dataclass(frozen=True)
class TradeRecord:
    execution_date: str
    symbol: str
    old_shares: float
    new_shares: float
    open_price: float
    old_notional: float
    new_notional: float
    trade_notional: float
    abs_trade_notional: float
    fee_component: float


@dataclass(frozen=True)
class RebalanceRecord:
    signal_session: str
    execution_session: str
    model_id: str
    cost_bps: float
    V_pre: float
    V_post: float
    fee: float
    solver_residual: float
    one_way_turnover: float
    gross_traded_notional: float
    gross_traded_ratio: float
    cash_before: float
    cash_after: float


@dataclass(frozen=True)
class DailyNavRecord:
    date: str
    nav: float
    daily_return: float
    cash: float
    gross_exposure: float
    rebalance_flag: bool
    fee: float


@dataclass(frozen=True)
class PositionRecord:
    date: str
    symbol: str
    shares: float
    close: float
    market_value: float
    weight: float


@dataclass(frozen=True)
class PendingExecutionRecord:
    signal_session: str
    execution_session: str
    model_id: str
    partition: str
    status: str
    reason: str

@dataclass(frozen=True)
class Stage9HoldoutAuthorization:
    """Narrow authorization token specifically for unlocking Holdout."""
    lock_v3_hash: str
    dataset_hash: str
    protocol_hash: str
    execution_manifest_hash: str
    signature: str
