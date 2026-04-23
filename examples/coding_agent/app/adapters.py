"""LLM adapter and memory manager — application-layer implementations.

These were removed from the nanoharness kernel because they depend on
external packages (openai, anthropic, etc.) and are not part of the
ETCSLV governance components. Each application provides its own.
"""

import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from openai import OpenAI

from nanoharness.core.schema import LLMResponse, ToolCall


# ── LLM Adapter ──

class OpenAIAdapter:
    """OpenAI-compatible LLM adapter (satisfies LLMProtocol)."""

    def __init__(
        self,
        api_key: str,
        model: str = "deepseek-chat",
        base_url: Optional[str] = None,
    ):
        self._client = OpenAI(api_key=api_key, base_url=base_url)
        self._model = model

    def chat(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
    ) -> LLMResponse:
        kwargs = {"model": self._model, "messages": messages}
        if tools:
            kwargs["tools"] = tools
        resp = self._client.chat.completions.create(**kwargs)
        choice = resp.choices[0]
        tool_calls = None
        if choice.message.tool_calls:
            tool_calls = [
                ToolCall(
                    name=tc.function.name,
                    arguments=json.loads(tc.function.arguments),
                )
                for tc in choice.message.tool_calls
            ]
        return LLMResponse(content=choice.message.content or "", tool_calls=tool_calls)


# ── Memory ──

class MemoryEntry:
    __slots__ = ("key", "content", "timestamp", "metadata")

    def __init__(self, key: str, content: str, timestamp: float = 0.0, metadata: Optional[Dict] = None):
        self.key = key
        self.content = content
        self.timestamp = timestamp
        self.metadata = metadata or {}


class SimpleMemoryManager:
    """Working memory (per-run scratchpad) + persistent memory (JSON file)."""

    def __init__(self, persist_path: str):
        self._working: Dict[str, Any] = {}
        self._long_term: List[MemoryEntry] = []
        self._persist_path = Path(persist_path)
        self._load()

    def store(self, key: str, content: str, **metadata) -> None:
        self._long_term.append(
            MemoryEntry(key=key, content=content, timestamp=time.time(), metadata=metadata)
        )
        self._save()

    def recall(self, query: str, top_k: int = 5) -> List[MemoryEntry]:
        q = query.lower()
        scored = [e for e in self._long_term if q in e.key.lower() or q in e.content.lower()]
        scored.sort(key=lambda e: e.timestamp, reverse=True)
        return scored[:top_k]

    def get_working(self) -> Dict[str, Any]:
        return self._working

    def set_working(self, data: Dict[str, Any]) -> None:
        self._working = data

    def clear_working(self):
        self._working.clear()

    def reset(self):
        self._working.clear()
        self._long_term.clear()
        if self._persist_path.exists():
            self._persist_path.unlink()

    def _save(self):
        self._persist_path.parent.mkdir(parents=True, exist_ok=True)
        self._persist_path.write_text(
            json.dumps(
                [{"key": e.key, "content": e.content, "timestamp": e.timestamp, "metadata": e.metadata}
                 for e in self._long_term],
                ensure_ascii=False, indent=2,
            ),
            encoding="utf-8",
        )

    def _load(self):
        if self._persist_path.exists():
            data = json.loads(self._persist_path.read_text(encoding="utf-8"))
            self._long_term = [MemoryEntry(**d) for d in data]
