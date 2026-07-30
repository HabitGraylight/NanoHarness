"""Durable, atomic JSON persistence for NanoLoop runs."""

import json
import threading
from pathlib import Path
from typing import List

from app.schema import LoopState


class JsonLoopStore:
    """Store each run as an independent JSON document.

    A same-directory temporary file plus ``replace`` keeps state updates atomic
    on the local filesystem. The lock protects multiple threads in one process;
    a production multi-host runner would add a real lease backend.
    """

    def __init__(self, root: str):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def save(self, state: LoopState) -> None:
        path = self._path(state.run_id)
        tmp_path = path.with_suffix(".json.tmp")
        payload = state.model_dump(mode="json")
        with self._lock:
            tmp_path.write_text(
                json.dumps(payload, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            tmp_path.replace(path)

    def load(self, run_id: str) -> LoopState:
        path = self._path(run_id)
        if not path.exists():
            raise KeyError(f"Loop run '{run_id}' not found")
        return LoopState.model_validate_json(path.read_text(encoding="utf-8"))

    def list_states(self) -> List[LoopState]:
        states = []
        for path in sorted(self.root.glob("*.json")):
            states.append(
                LoopState.model_validate_json(path.read_text(encoding="utf-8"))
            )
        return states

    def exists(self, run_id: str) -> bool:
        return self._path(run_id).exists()

    def _path(self, run_id: str) -> Path:
        if not run_id or any(ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for ch in run_id):
            raise ValueError(f"Invalid run id: {run_id!r}")
        return self.root / f"{run_id}.json"
