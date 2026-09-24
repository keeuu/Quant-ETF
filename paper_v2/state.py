import json
import os
import fcntl
from typing import Dict, Any, Optional
from .config import STATE_FILE_PATH, INITIAL_CASH, MODELS

class PaperState:
    def __init__(self):
        self.state_path = STATE_FILE_PATH
        self._state = self._load()
        
    def _load(self) -> Dict[str, Any]:
        if not os.path.exists(self.state_path):
            return self._init_state()
        with open(self.state_path, "r") as f:
            return json.load(f)
            
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
        for model in MODELS:
            state["models"][model.model_id] = {
                "cash": INITIAL_CASH,
                "shares": {},
                "nav": INITIAL_CASH,
                "last_signal": None,
                "last_execution": None,
                "pending_order": None,
                "fees": 0.0,
                "turnover": 0.0
            }
        return state
        
    def save(self):
        with open(self.state_path, "w") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                json.dump(self._state, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)
                
    def get_model_state(self, model_id: str) -> Dict[str, Any]:
        return self._state["models"][model_id]
        
    def update_model_state(self, model_id: str, updates: Dict[str, Any]):
        self._state["models"][model_id].update(updates)
        
    def set_pending_order(self, model_id: str, order_info: Optional[Dict[str, Any]]):
        self._state["models"][model_id]["pending_order"] = order_info
        
    @property
    def meta(self):
        return self._state["meta"]
