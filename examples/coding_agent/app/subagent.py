"""Subagent system for delegating focused subtasks.

Structure:
    SubagentContext  — isolated messages, tool subset, handlers, max_turns
    run_subagent()   — executes a Think→Act→Observe loop in isolation
    task tool        — parent agent delegates via this single entry point

Use cases:
    1. Reduce parent context — intermediate noise stays in the subagent
    2. Focused prompts — "read these files, give me a one-line summary"
    3. Foundation for multi-agent collaboration
"""

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List

from app.dispatch import DispatchRegistry, tool_result


# ── Configuration ──

SUBAGENT_SYSTEM_PROMPT = (
    "You are a focused assistant handling a specific subtask.\n"
    "Complete the task efficiently and return a concise summary.\n"
    "You are in read-only mode — do not modify any files."
)

# Tools the subagent is allowed to use (read-only subset)
SUBAGENT_TOOL_WHITELIST = frozenset({
    "file_read", "file_list", "file_find",
    "search_code", "list_files",
})


# ── Core ──


@dataclass
class SubagentContext:
    """Isolated context for a single subagent run.

    messages:  subagent's own conversation history
    tools:     name → JSON schema   (what the LLM sees)
    handlers:  name → (args: Dict) → str   (what executes)
    max_turns: safety limit
    """
    messages: List[Dict[str, Any]] = field(default_factory=list)
    tools: Dict[str, Dict] = field(default_factory=dict)
    handlers: Dict[str, Callable] = field(default_factory=dict)
    max_turns: int = 8


def build_subagent_context(
    registry: DispatchRegistry,
    max_turns: int = 8,
) -> SubagentContext:
    """Build a subagent context with a read-only tool subset from the main registry."""
    tools = {}
    handlers = {}
    for name in SUBAGENT_TOOL_WHITELIST:
        if name in registry.dispatch_map:
            tools[name] = registry.schemas[name]
            handlers[name] = _make_safe_handler(registry, name)
    return SubagentContext(
        messages=[],
        tools=tools,
        handlers=handlers,
        max_turns=max_turns,
    )


def run_subagent(
    prompt: str,
    llm_client,
    ctx: SubagentContext,
) -> str:
    """Run a subagent with its own isolated context.

    Executes a Think→Act→Observe loop. Returns a summary string.
    On max turns exceeded, forces a final summary.
    On error, returns a description of the failure.
    """
    # Fresh context for each run
    ctx.messages = [
        {"role": "system", "content": SUBAGENT_SYSTEM_PROMPT},
        {"role": "user", "content": prompt},
    ]

    for turn in range(ctx.max_turns):
        # Think
        tool_schemas = list(ctx.tools.values()) if ctx.tools else None
        response = llm_client.chat(ctx.messages, tools=tool_schemas)

        # Record assistant message
        assistant_msg = {"role": "assistant", "content": response.content or ""}
        if response.tool_calls:
            assistant_msg["tool_calls"] = [tc.model_dump() for tc in response.tool_calls]
        ctx.messages.append(assistant_msg)

        # No tool calls → subagent is done, return as summary
        if not response.tool_calls:
            return (response.content or "").strip()

        # Act — execute each tool call
        for call in response.tool_calls:
            handler = ctx.handlers.get(call.name)
            if handler is None:
                obs = f"Error: Unknown tool '{call.name}'"
            else:
                try:
                    obs = handler(call.arguments)
                except Exception as e:
                    obs = f"Error: {e}"
            ctx.messages.append({"role": "tool", "content": str(obs)})

    # Max turns exceeded — force a summary
    ctx.messages.append({
        "role": "user",
        "content": "You have reached the maximum number of turns. "
                   "Provide a brief summary of what you found so far.",
    })
    final = llm_client.chat(ctx.messages, tools=None)
    return (final.content or "").strip()


# ── Task tool (parent agent entry point) ──


def register_task_tool(registry: DispatchRegistry, llm_client):
    """Register the 'task' tool on the main registry.

    When the parent agent calls task(description="..."),
    a read-only subagent is spawned to handle it.
    """
    def task_handler(args: Dict) -> tool_result:
        prompt = args.get("description", "")
        if not prompt:
            return tool_result(ok=False, output="", error="No task description provided")
        try:
            ctx = build_subagent_context(registry)
            summary = run_subagent(prompt, llm_client, ctx)
            return tool_result(ok=True, output=summary)
        except Exception as e:
            return tool_result(ok=False, output="", error=f"Subagent error: {e}")

    registry.register(
        name="task",
        handler=task_handler,
        schema={
            "type": "function",
            "function": {
                "name": "task",
                "description": (
                    "Delegate a focused read-only subtask to a subagent. "
                    "The subagent can read files, search code, and list directories, "
                    "but cannot modify anything. Use this to gather information "
                    "without cluttering the main conversation."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "description": {
                            "type": "string",
                            "description": (
                                "Clear, specific description of the subtask. "
                                "Examples: 'Read main.py and summarize its structure', "
                                "'Check if there are tests for the auth module'"
                            ),
                        },
                    },
                    "required": ["description"],
                },
            },
        },
        path_params=[],  # task tool has no path params
    )


# ── Internal helpers ──


def _make_safe_handler(registry: DispatchRegistry, name: str) -> Callable:
    """Wrap registry.call() into a safe (Dict) -> string function for subagent use."""
    def handler(args: Dict) -> str:
        try:
            return registry.call(name, args)
        except Exception as e:
            return f"Error: {e}"
    return handler
