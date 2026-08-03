"""Reusable Memory extension with schema-first tool installation."""

from typing import Any, Dict

from pydantic import BaseModel, ConfigDict, Field

from nanoharness.extensions.base import (
    BaseExtension,
    ExtensionContext,
    ExtensionInstallation,
    ExtensionManifest,
)
from nanoharness.extensions.memory.manager import FileMemoryManager


class MemoryExtensionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    directory: str = ".memory"
    service_name: str = "memory"
    tool_prefix: str = ""
    preview_chars: int = Field(default=300, ge=1, le=10000)


def _tool_name(prefix: str, name: str) -> str:
    return f"{prefix}{name}" if prefix else name


def register_memory_tools(
    registry,
    memory: FileMemoryManager,
    *,
    tool_prefix: str = "",
    preview_chars: int = 300,
) -> list[str]:
    """Install memory tools on any registry exposing schema-first register()."""

    def save_memory(args: Dict[str, Any]) -> str:
        topic = args["topic"]
        filename = memory.save(
            topic,
            args["content"],
            description=args.get("description", ""),
            type=args.get("type", "note"),
        )
        return f"Saved memory '{topic}' → {filename}.md"

    def recall_memory(args: Dict[str, Any]) -> str:
        results = memory.recall(args["query"], args.get("top_k", 5))
        if not results:
            return "No matching memories found."
        parts = []
        for entry in results:
            preview = entry.content[:preview_chars]
            if len(entry.content) > preview_chars:
                preview += "..."
            parts.append(f"## {entry.name}\n{preview}")
        return "\n\n---\n".join(parts)

    def list_memories(args: Dict[str, Any]) -> str:
        entries = memory.list_all()
        if not entries:
            return "No memories stored yet."
        return "\n".join(
            f"- [{entry.name}] {entry.description}"
            if entry.description
            else f"- [{entry.name}]"
            for entry in entries
        )

    names = {
        "save": _tool_name(tool_prefix, "save_memory"),
        "recall": _tool_name(tool_prefix, "recall_memory"),
        "list": _tool_name(tool_prefix, "list_memories"),
    }
    registry.register(
        name=names["save"],
        handler=save_memory,
        schema={
            "type": "function",
            "function": {
                "name": names["save"],
                "description": (
                    "Save important information to long-term memory. Use this "
                    "for preferences, conventions, and durable project context."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "topic": {"type": "string", "description": "Short topic name"},
                        "content": {"type": "string", "description": "Markdown content"},
                        "description": {"type": "string", "description": "Index summary"},
                        "type": {
                            "type": "string",
                            "enum": ["note", "feedback", "reference", "project"],
                            "description": "Memory category",
                        },
                    },
                    "required": ["topic", "content"],
                },
            },
        },
    )
    registry.register(
        name=names["recall"],
        handler=recall_memory,
        schema={
            "type": "function",
            "function": {
                "name": names["recall"],
                "description": "Search long-term memories by keyword.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Search keyword"},
                        "top_k": {"type": "integer", "description": "Maximum results"},
                    },
                    "required": ["query"],
                },
            },
        },
    )
    registry.register(
        name=names["list"],
        handler=list_memories,
        schema={
            "type": "function",
            "function": {
                "name": names["list"],
                "description": "List all stored memories.",
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "required": [],
                },
            },
        },
    )
    return list(names.values())


class MemoryExtension(BaseExtension):
    manifest = ExtensionManifest(
        name="memory.file",
        version="1.0.0",
        description="Markdown file memory with save, recall, and list tools.",
        provides=["memory.store", "tools.memory"],
    )
    config_model = MemoryExtensionConfig

    def install(
        self,
        context: ExtensionContext,
        config: BaseModel,
    ) -> ExtensionInstallation:
        if not isinstance(config, MemoryExtensionConfig):
            raise TypeError("MemoryExtension requires MemoryExtensionConfig")

        expected_tools = {
            _tool_name(config.tool_prefix, "save_memory"),
            _tool_name(config.tool_prefix, "recall_memory"),
            _tool_name(config.tool_prefix, "list_memories"),
        }
        conflicts = sorted(expected_tools & context.tool_names())
        if conflicts:
            raise ValueError(f"MemoryExtension tool conflicts: {conflicts}")
        if config.service_name in context.services:
            raise ValueError(
                f"MemoryExtension service conflicts: {config.service_name!r}"
            )

        memory = FileMemoryManager(config.directory)
        tool_names = register_memory_tools(
            context,
            memory,
            tool_prefix=config.tool_prefix,
            preview_chars=config.preview_chars,
        )
        context.provide_service(config.service_name, memory)
        return ExtensionInstallation(
            name=self.manifest.name,
            version=self.manifest.version,
            capabilities=list(self.manifest.provides),
            tools=tool_names,
            services=[config.service_name],
            config=config.model_dump(mode="json"),
            metadata={"directory": str(memory.directory)},
        )
