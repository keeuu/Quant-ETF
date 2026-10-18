import json
import os
from typing import Dict, Any, Optional
from .fsutil import atomic_write_text, file_lock
from .config import STATE_FILE_PATH, INITIAL_CASH, ALL_MODELS, CANDIDATE_MODELS

class PaperState:
    def __init__(self):
        self.state_path = STATE_FILE_PATH
        self._state = self._load()
        
    def _load(self) -> Dict[str, Any]:
        if not os.path.exists(self.state_path):
            return self._init_state()
        with open(self.state_path, "r") as f:
            state = json.load(f)
        # An older state file may predate shadow candidates: add missing model entries / fields.
        candidate_ids = {m.model_id for m in CANDIDATE_MODELS}
        for model in ALL_MODELS:
            fresh = self._new_model_state(model.model_id in candidate_ids)
            existing = state["models"].setdefault(model.model_id, fresh)
            if model.model_id in candidate_ids:
                for k, v in fresh.items():
                    existing.setdefault(k, v)
        return state

    @staticmethod
    def _new_model_state(candidate: bool = False) -> Dict[str, Any]:
        ms = {
            "cash": INITIAL_CASH,
            "shares": {},
            "nav": INITIAL_CASH,
            "last_signal": None,
            "last_execution": None,
            "pending_order": None,
            "fees": 0.0,
            "turnover": 0.0,
            "dividends_total": 0.0,
            "dividends_credited": {},
            "last_dividend_date": None
        }
        if candidate:
            ms.update({"start_session": None, "last_accrual_session": None, "cash_interest_total": 0.0})
        return ms
            
    def _init_state(self) -> Dict[str, Any]:
        state = {
            "meta": {
                "activation_timestamp": None,
                "activation_git_commit": None,
                "activation_config_hash": None,
                "completed_trading_sessions": 0,
                "completed_months": 0
            },
            "models": {}
        }
        candidate_ids = {m.model_id for m in CANDIDATE_MODELS}
        for model in ALL_MODELS:
            state["models"][model.model_id] = self._new_model_state(model.model_id in candidate_ids)
        return state
        
    def save(self):
        """Atomic save: temp file in same dir + fsync + os.replace, under an exclusive lock."""
        payload = json.dumps(self._state, indent=2)
        with file_lock(self.state_path + ".lock"):
            atomic_write_text(self.state_path, payload)

    def get_model_state(self, model_id: str) -> Dict[str, Any]:
        return self._state["models"][model_id]
        
    def update_model_state(self, model_id: str, updates: Dict[str, Any]):
        self._state["models"][model_id].update(updates)
        
    def set_pending_order(self, model_id: str, order_info: Optional[Dict[str, Any]]):
        self._state["models"][model_id]["pending_order"] = order_info
        
    @property
    def meta(self):
        return self._state["meta"]
