import json
import os
import fcntl
from typing import Any, Dict, List

class AppendOnlyJournal:
    def __init__(self, filepath: str):
        self.filepath = filepath
        
    def append(self, event: Dict[str, Any]):
        with open(self.filepath, "a") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                f.write(json.dumps(event) + "\n")
                f.flush()
                os.fsync(f.fileno())
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)
                
    def read_all(self) -> List[Dict[str, Any]]:
        if not os.path.exists(self.filepath):
            return []
        events = []
        with open(self.filepath, "r") as f:
            for line in f:
                if line.strip():
                    events.append(json.loads(line))
        return events
