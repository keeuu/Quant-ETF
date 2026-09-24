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

UNIVERSE = ["SPY", "QQQ", "IWM", "EFA", "EEM", "GLD", "TLT", "IEF"]
BENCHMARK = "SPY"
INITIAL_CASH = 100000.0
COST_BPS = 5.0

PAPER_V2_STATE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "paper_v2_state"))
SIGNAL_JOURNAL_PATH = os.path.join(PAPER_V2_STATE_DIR, "signal_journal.jsonl")
EXEC_JOURNAL_PATH = os.path.join(PAPER_V2_STATE_DIR, "execution_journal.jsonl")
NAV_JOURNAL_PATH = os.path.join(PAPER_V2_STATE_DIR, "daily_nav.jsonl")
STATE_FILE_PATH = os.path.join(PAPER_V2_STATE_DIR, "state.json")
DATA_SNAPSHOTS_DIR = os.path.join(PAPER_V2_STATE_DIR, "snapshots")

def setup_dirs():
    os.makedirs(PAPER_V2_STATE_DIR, exist_ok=True)
    os.makedirs(DATA_SNAPSHOTS_DIR, exist_ok=True)
