import os
from typing import NamedTuple

class ModelSpec(NamedTuple):
    model_id: str
    cadence: str
    momentum_months: int
    trend_weeks: int

MODELS = []
for cadence in ["W", "M"]:
    for m in [6, 9, 12]:
        for t in [36, 40, 44]:
            model_id = f"{cadence}_M{m:02d}_T{t}"
            MODELS.append(ModelSpec(model_id, cadence, m, t))

# Frozen research_v3 candidate shadowed alongside MODELS (paper only). It is NOT part of MODELS so the
# existing 18 models behave exactly as before; parameters are cross-checked against
# research_v3/candidates_frozen.json in paper_v2.candidate.
CANDIDATE_LABEL = "CAP_DEF_M"
CANDIDATE_MODEL = ModelSpec(CANDIDATE_LABEL, "M", 12, 40)
CANDIDATE_MODELS = [CANDIDATE_MODEL]
ALL_MODELS = MODELS + CANDIDATE_MODELS
FREEZE_DATE = "2026-10-19"
CANDIDATE_COST_BPS = 5.0
CANDIDATE_SLIPPAGE_BPS = 5.0
CANDIDATE_EFFECTIVE_COST_BPS = CANDIDATE_COST_BPS + CANDIDATE_SLIPPAGE_BPS

UNIVERSE = ["SPY", "QQQ", "IWM", "EFA", "EEM", "GLD", "TLT", "IEF"]
BENCHMARK = "SPY"
INITIAL_CASH = 100000.0
COST_BPS = 5.0

# State directory; override with PAPER_V2_STATE_DIR (used by tests and offline runs).
PAPER_V2_STATE_DIR = os.path.abspath(
    os.environ.get("PAPER_V2_STATE_DIR")
    or os.path.join(os.path.dirname(__file__), "..", "paper_v2_state")
)
SIGNAL_JOURNAL_PATH = os.path.join(PAPER_V2_STATE_DIR, "signal_journal.jsonl")
EXEC_JOURNAL_PATH = os.path.join(PAPER_V2_STATE_DIR, "execution_journal.jsonl")
NAV_JOURNAL_PATH = os.path.join(PAPER_V2_STATE_DIR, "daily_nav.jsonl")
STATE_FILE_PATH = os.path.join(PAPER_V2_STATE_DIR, "state.json")
DATA_SNAPSHOTS_DIR = os.path.join(PAPER_V2_STATE_DIR, "snapshots")

def setup_dirs():
    os.makedirs(PAPER_V2_STATE_DIR, exist_ok=True)
    os.makedirs(DATA_SNAPSHOTS_DIR, exist_ok=True)


def config_dict() -> dict:
    """The configuration actually used by the shadow run (input to config_hash)."""
    from .candidate import candidates_frozen_sha256
    return {
        "models": [m._asdict() for m in MODELS],
        "candidates": [
            {**m._asdict(), "frozen_sha256": candidates_frozen_sha256(), "freeze_date": FREEZE_DATE,
             "cost_bps": CANDIDATE_COST_BPS, "slippage_bps": CANDIDATE_SLIPPAGE_BPS,
             "cash_yield_source": "irx"}
            for m in CANDIDATE_MODELS
        ],
        "universe": list(UNIVERSE),
        "benchmark": BENCHMARK,
        "initial_cash": INITIAL_CASH,
        "cost_bps": COST_BPS,
    }
