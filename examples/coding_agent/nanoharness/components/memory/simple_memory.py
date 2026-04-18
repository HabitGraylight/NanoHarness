import json
import time
from pathlib import Path
from typing import Any, Dict, List

from nanoharness.core.base import BaseMemoryManager
from nanoharness.core.prompt import PromptManager
from nanoharness.core.schema import MemoryEntry


class SimpleMemoryManager(BaseMemoryManager):
    """Working memory (per-run scratchpad) + persistent memory (JSON file).

    Working memory is a plain dict that lives in RAM and is cleared each run.
    Persistent memory is a list of MemoryEntry objects serialized to JSON.
    Recall uses keyword matching on key and content fields.
    """

    def __init__(self, persist_path: str = "memory.json", prompts: PromptManager = None):
        self._working: Dict[str, Any] = {}
        self._long_term: List[MemoryEntry] = []
        self._persist_path = Path(persist_path)
        self.prompts = prompts or PromptManager()
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
        # Return most recent matches
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


class MemoryToolMixin:
    """Mixin that registers memory_store / memory_recall as agent-callable tools.

    Usage:
        registry = DictToolRegistry()
        memory = SimpleMemoryManager()
        MemoryToolMixin.register(memory, registry)
    """

    @staticmethod
    def register(memory: SimpleMemoryManager, tool_registry, prompts: PromptManager = None):
        """Register memory tools onto an existing tool registry."""
        pm = prompts or memory.prompts

        def memory_store(key: str, content: str):
            """Store a piece of information in long-term memory for later recall."""
            memory.store(key, content)
            return pm.render("tool.memory_store.success", key=key)

        def memory_recall(query: str, top_k: int = 5):
            """Recall relevant memories by searching with a keyword."""
            results = memory.recall(query, top_k)
            if not results:
                return pm.get("tool.memory_recall.empty")
            return "\n".join(
                pm.render("tool.memory_recall.entry", key=e.key, content=e.content)
                for e in results
            )

        tool_registry.tool(memory_store)
        tool_registry.tool(memory_recall)
