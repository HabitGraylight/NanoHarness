import json
from pathlib import Path
from typing import Any, Dict

from nanoharness.core.base import BaseStateStore


class JsonStateStore(BaseStateStore):
    """Minimal persistence layer using a local JSON file."""

    def __init__(self, storage_path: str = "agent_state.json"):
        self._path = Path(storage_path)

    def save_state(self, state: Dict[str, Any]):
        self._path.write_text(
            json.dumps(state, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def load_state(self) -> Dict[str, Any]:
        if self._path.exists():
            return json.loads(self._path.read_text(encoding="utf-8"))
        return {}

    def reset(self):
        self._path.unlink(missing_ok=True)
