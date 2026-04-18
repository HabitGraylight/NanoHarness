"""Mixin that registers memory_store / memory_recall as agent-callable tools.

This is an app-layer utility — the decision to expose memory as tools
belongs to the application, not the kernel.
"""

from nanoharness.components.tools.dict_registry import DictToolRegistry
from nanoharness.components.memory.simple_memory import SimpleMemoryManager


class MemoryToolMixin:
    @staticmethod
    def register(memory: SimpleMemoryManager, tool_registry: DictToolRegistry):
        """Register memory_store / memory_recall onto an existing tool registry."""

        def memory_store(key: str, content: str):
            """Store a piece of information in long-term memory for later recall."""
            memory.store(key, content)
            return f"Stored under key '{key}'."

        def memory_recall(query: str, top_k: int = 5):
            """Recall relevant memories by searching with a keyword."""
            results = memory.recall(query, top_k)
            if not results:
                return "No matching memories found."
            return "\n".join(
                f"[{e.key}] {e.content}" for e in results
            )

        tool_registry.tool(memory_store)
        tool_registry.tool(memory_recall)
