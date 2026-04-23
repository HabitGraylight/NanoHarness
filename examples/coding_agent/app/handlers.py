"""Tool definitions: schemas, path params, and handler bindings.

Adding a tool = adding a handler + adding a schema.
All filesystem tools go through bash. Memory tools are in-process.
"""

import shlex
from pathlib import Path
from typing import Dict, List

from nanoharness.components.tools.script_tools import _parse_script

from app.dispatch import DispatchRegistry, bash_handler, bash_wrap, inprocess_handler


# ── Path param declarations ──

_FILE_PATH_TOOLS = {"file_read", "file_write", "file_edit", "file_list", "file_find"}
_GIT_PATH_TOOLS = {
    "git_add", "git_branch_create", "git_branch_list", "git_checkout",
    "git_commit", "git_diff", "git_init", "git_log", "git_merge",
    "git_pull", "git_push", "git_remote_list", "git_reset", "git_revert",
    "git_show", "git_stash", "git_stash_list", "git_stash_pop", "git_status",
}


def _path_params_for(tool_name: str) -> List[str]:
    """Declare which params are filesystem paths for a given tool."""
    if tool_name in _FILE_PATH_TOOLS:
        return ["path"]
    if tool_name in _GIT_PATH_TOOLS:
        return ["repo_path"]
    return []


# ── Script tools ──


def register_script_tools(registry: DispatchRegistry, scripts_dir: str):
    """Load all .sh scripts and register them with the dispatch registry."""
    scripts_path = Path(scripts_dir)
    if not scripts_path.is_dir():
        return

    for script_path in sorted(scripts_path.glob("*.sh")):
        tool_name = script_path.stem
        meta = _parse_script(script_path)

        properties = {}
        required = []
        for p in meta["params"]:
            properties[p["name"]] = {"type": p["type"]}
            if p["required"]:
                required.append(p["name"])

        schema = {
            "type": "function",
            "function": {
                "name": tool_name,
                "description": meta["description"],
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": required,
                },
            },
        }

        handler = bash_handler(str(script_path), timeout=registry._timeout)
        registry.register(
            name=tool_name,
            handler=handler,
            schema=schema,
            path_params=_path_params_for(tool_name),
        )


# ── Python → bash tools ──


def register_python_tools(registry: DispatchRegistry):
    """Register search_code and list_files as bash-wrapped handlers."""

    registry.register(
        name="search_code",
        handler=bash_wrap(_search_code_builder, timeout=30),
        schema={
            "type": "function",
            "function": {
                "name": "search_code",
                "description": (
                    "Search for a regex pattern in source files (like grep). "
                    "Use this to find where functions, classes, or variables are defined "
                    "or referenced across the codebase."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "pattern": {"type": "string", "description": "Regex pattern to search for"},
                        "path": {"type": "string", "description": "Directory to search in"},
                        "file_glob": {"type": "string", "description": "File pattern to include"},
                    },
                    "required": ["pattern"],
                },
            },
        },
        path_params=["path"],
    )

    registry.register(
        name="list_files",
        handler=bash_wrap(_list_files_builder, timeout=30),
        schema={
            "type": "function",
            "function": {
                "name": "list_files",
                "description": (
                    "List files matching a glob pattern. "
                    "Use this to understand the project structure before making changes."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "pattern": {"type": "string", "description": "Glob pattern to match"},
                        "path": {"type": "string", "description": "Directory to search in"},
                    },
                    "required": [],
                },
            },
        },
        path_params=["path"],
    )


def _search_code_builder(args: Dict) -> List[str]:
    """Build a grep command wrapped in bash with output truncation."""
    pattern = shlex.quote(args["pattern"])
    file_glob = shlex.quote(args.get("file_glob", "*.py"))
    path = shlex.quote(args.get("path", "."))
    return [
        "bash", "-c",
        f"grep -rn -E --include {file_glob} {pattern} {path} | head -50",
    ]


def _list_files_builder(args: Dict) -> List[str]:
    """Build a find command wrapped in bash with output truncation."""
    # Extract the suffix for -name matching (e.g. "**/*.py" → "*.py")
    raw_pattern = args.get("pattern", "**/*.py")
    name_part = raw_pattern.rsplit("/", 1)[-1] if "/" in raw_pattern else raw_pattern
    name_glob = shlex.quote(name_part)
    path = shlex.quote(args.get("path", "."))
    return [
        "bash", "-c",
        f"find {path} -name {name_glob} -type f 2>/dev/null | sort | head -80",
    ]


# ── Memory tools (in-process) ──


def register_memory_tools(registry: DispatchRegistry, memory):
    """Register save_memory, recall_memory, and list_memories tools."""

    def save_memory(topic: str, content: str, description: str = "",
                    type: str = "note") -> str:
        filename = memory.save(topic, content, description=description, type=type)
        return f"Saved memory '{topic}' → {filename}.md"

    def recall_memory(query: str, top_k: int = 5) -> str:
        results = memory.recall(query, top_k)
        if not results:
            return "No matching memories found."
        parts = []
        for e in results:
            preview = e.content[:300] + ("..." if len(e.content) > 300 else "")
            parts.append(f"## {e.name}\n{preview}")
        return "\n\n---\n".join(parts)

    def list_memories() -> str:
        entries = memory.list_all()
        if not entries:
            return "No memories stored yet."
        return "\n".join(
            f"- [{e.name}] {e.description}" if e.description else f"- [{e.name}]"
            for e in entries
        )

    registry.register(
        name="save_memory",
        handler=inprocess_handler(save_memory),
        schema={
            "type": "function",
            "function": {
                "name": "save_memory",
                "description": (
                    "Save important information to long-term memory. "
                    "Use this when the user mentions preferences, conventions, "
                    "project context, or anything worth remembering across sessions."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "topic": {
                            "type": "string",
                            "description": "Short topic name (e.g. 'prefer_tabs', 'feedback_tests')",
                        },
                        "content": {
                            "type": "string",
                            "description": "Full content to remember (markdown OK)",
                        },
                        "description": {
                            "type": "string",
                            "description": "One-line summary for the index",
                        },
                        "type": {
                            "type": "string",
                            "enum": ["note", "feedback", "reference", "project"],
                            "description": "Category of this memory",
                        },
                    },
                    "required": ["topic", "content"],
                },
            },
        },
    )

    registry.register(
        name="recall_memory",
        handler=inprocess_handler(recall_memory),
        schema={
            "type": "function",
            "function": {
                "name": "recall_memory",
                "description": "Search long-term memories by keyword.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Search keyword"},
                        "top_k": {"type": "integer", "description": "Max results (default 5)"},
                    },
                    "required": ["query"],
                },
            },
        },
    )

    registry.register(
        name="list_memories",
        handler=inprocess_handler(list_memories),
        schema={
            "type": "function",
            "function": {
                "name": "list_memories",
                "description": "List all stored memories.",
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "required": [],
                },
            },
        },
    )
