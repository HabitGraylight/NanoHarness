"""NanoClaudeCode exports for the reusable Subagent extension."""

from nanoharness.extensions.subagents import (
    FORK_SYSTEM_ADDON,
    SUBAGENT_SYSTEM_PROMPT,
    SUBAGENT_TOOL_WHITELIST,
    SubagentContext,
    SubagentRunner,
    build_subagent_context,
    register_task_tool,
    run_subagent,
)

__all__ = [
    "FORK_SYSTEM_ADDON",
    "SUBAGENT_SYSTEM_PROMPT",
    "SUBAGENT_TOOL_WHITELIST",
    "SubagentContext",
    "SubagentRunner",
    "build_subagent_context",
    "register_task_tool",
    "run_subagent",
]
