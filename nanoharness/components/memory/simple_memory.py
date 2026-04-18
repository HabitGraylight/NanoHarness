import json
import time
from pathlib import Path
from typing import Any, Dict, List

from nanoharness.core.base import BaseMemoryManager
from nanoharness.core.schema import MemoryEntry


class SimpleMemoryManager(BaseMemoryManager):
    """Working memory (per-run scratchpad) + persistent memory (JSON file).

    Working memory is a plain dict that lives in RAM and is cleared per-run.
    Persistent memory is a list of MemoryEntry objects serialized to JSON.
    Recall uses keyword matching on key and content fields.
    """

    def __init__(self, persist_path: str):
        self._working: Dict[str, Any] = {}
        self._long_term: List[MemoryEntry] = []
        self._persist_path = Path(persist_path)
        self._load()

    # ── Persistent memory ──

    def store(self, key: str, content: str, **metadata) -> None:
        entry = MemoryEntry(
            key=key,
            content=content,
            timestamp=time.time(),
            metadata=metadata,
        )
        self._long_term.append(entry)
        self._save()

    def recall(self, query: str, top_k: int = 5) -> List[MemoryEntry]:
        q = query.lower()
        scored = []
        for entry in self._long_term:
            if q in entry.key.lower() or q in entry.content.lower():
                scored.append(entry)
        scored.sort(key=lambda e: e.timestamp, reverse=True)
        return scored[:top_k]

    # ── Working memory ──

    def get_working(self) -> Dict[str, Any]:
        return self._working

    def set_working(self, data: Dict[str, Any]) -> None:
        self._working = data

    # ── Lifecycle ──

    def clear_working(self):
        self._working.clear()

    def reset(self):
        self._working.clear()
        self._long_term.clear()
        if self._persist_path.exists():
            self._persist_path.unlink()

    # ── Persistence ──

    def _save(self):
        self._persist_path.write_text(
            json.dumps(
                [e.model_dump() for e in self._long_term],
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    def _load(self):
        if self._persist_path.exists():
            data = json.loads(self._persist_path.read_text(encoding="utf-8"))
            self._long_term = [MemoryEntry(**d) for d in data]
