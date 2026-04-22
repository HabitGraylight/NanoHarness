"""Coding agent hooks: lifecycle display + tool execution interception.

Two hook systems:
    1. SimpleHookManager — lifecycle events (task start/end, step end, thought)
    2. ToolHookRunner — pre/post tool execution interception

Tool hook pipeline:
    model → tool_use
        │
        ▼
    PreToolUse hooks
        ├─ BLOCK (exit 1)  → stop, return error as observation
        ├─ INJECT (exit 2) → add a message for the model, then continue
        └─ CONTINUE (exit 0) → proceed normally
        │
        ▼
    execute tool
        │
        ▼
    PostToolUse hooks
        ├─ INJECT (exit 2) → append supplementary note to observation
        └─ CONTINUE (exit 0) → normal finish
"""

import fnmatch
from dataclasses import dataclass
from enum import IntEnum
from typing import Any, Callable, Dict, List, Optional, Tuple

from nanoharness.components.hooks.simple_hooks import SimpleHookManager
from nanoharness.core.base import HookStage

# ── ANSI colors ──

_BOLD = "\033[1m"
_DIM = "\033[2m"
_CYAN = "\033[36m"
_GREEN = "\033[32m"
_YELLOW = "\033[33m"
_RED = "\033[31m"
_BLUE = "\033[34m"
_RESET = "\033[0m"


# ═══════════════════════════════════════════════════════
#  Tool Hook Runner (pre/post tool execution)
# ═══════════════════════════════════════════════════════


class HookAction(IntEnum):
    """Exit codes for tool hooks."""
    CONTINUE = 0   # proceed normally
    BLOCK = 1      # stop tool execution
    INJECT = 2     # add a message, then proceed


@dataclass
class HookDecision:
    """Return value from a hook function."""
    action: HookAction
    message: Optional[str] = None


# Type aliases for hook function signatures
PreHookFn = Callable[[str, Dict], Optional[HookDecision]]
PostHookFn = Callable[[str, Dict, str], Optional[HookDecision]]


class ToolHookRunner:
    """Runs pre/post tool hooks matched by glob patterns.

    Usage:
        runner = ToolHookRunner()
        runner.register_pre("shell_exec", my_pre_hook)
        runner.register_post("file_read", my_post_hook)

        # Before tool execution
        decision = runner.run_pre("shell_exec", {"command": "rm -rf /"})
        if decision and decision.action == HookAction.BLOCK:
            ...  # stop

        # After tool execution
        decision = runner.run_post("file_read", {"path": "x"}, "output...")
        if decision and decision.action == HookAction.INJECT:
            obs += decision.message
    """

    def __init__(self):
        self._pre_hooks: List[Tuple[str, PreHookFn]] = []
        self._post_hooks: List[Tuple[str, PostHookFn]] = []

    def register_pre(self, pattern: str, hook: PreHookFn):
        """Register a pre-tool hook. pattern is a glob (e.g. 'shell_exec', 'git_*')."""
        self._pre_hooks.append((pattern, hook))

    def register_post(self, pattern: str, hook: PostHookFn):
        """Register a post-tool hook."""
        self._post_hooks.append((pattern, hook))

    def run_pre(self, tool_name: str, args: Dict) -> Optional[HookDecision]:
        """Run all matching pre-hooks. First non-None decision wins."""
        for pattern, hook in self._pre_hooks:
            if fnmatch.fnmatch(tool_name, pattern):
                result = hook(tool_name, args)
                if result is not None:
                    return result
        return None

    def run_post(self, tool_name: str, args: Dict, result: str) -> Optional[HookDecision]:
        """Run all matching post-hooks. First non-None decision wins."""
        for pattern, hook in self._post_hooks:
            if fnmatch.fnmatch(tool_name, pattern):
                decision = hook(tool_name, args, result)
                if decision is not None:
                    return decision
        return None

    def reset(self):
        self._pre_hooks.clear()
        self._post_hooks.clear()


# ═══════════════════════════════════════════════════════
#  Lifecycle hooks (terminal display)
# ═══════════════════════════════════════════════════════


def build_hooks() -> SimpleHookManager:
    """Build lifecycle hook manager with formatted terminal output."""
    hooks = SimpleHookManager()
    hooks.register(HookStage.ON_TASK_START, _on_task_start)
    hooks.register(HookStage.ON_THOUGHT_READY, _on_thought)
    hooks.register(HookStage.ON_STEP_END, _on_step_end)
    hooks.register(HookStage.ON_TASK_END, _on_task_end)
    return hooks


def _on_task_start(query):
    print(f"\n{_BOLD}{'━' * 60}{_RESET}")
    print(f"{_BOLD}{_CYAN}  Task:{_RESET} {_BOLD}{query}{_RESET}")
    print(f"{_BOLD}{'━' * 60}{_RESET}\n")


def _on_thought(response):
    if response.content:
        content = response.content.strip()
        if len(content) > 500:
            content = content[:500] + f"{_DIM}... ({len(response.content)} chars){_RESET}"
        print(f"  {_DIM}Thinking:{_RESET} {content}")

    if response.tool_calls:
        for tc in response.tool_calls:
            args_parts = []
            for k, v in tc.arguments.items():
                val = str(v)
                if len(val) > 60:
                    val = val[:60] + "..."
                args_parts.append(f"{k}={val}")
            args_str = ", ".join(args_parts)
            print(f"  {_YELLOW}▶ {tc.name}{_RESET}({_DIM}{args_str}{_RESET})")


def _on_step_end(step_result):
    icon_map = {
        "success": f"{_GREEN}✓{_RESET}",
        "error": f"{_RED}✗{_RESET}",
        "terminated": f"{_BLUE}●{_RESET}",
    }
    icon = icon_map.get(step_result.status, "?")

    if step_result.observation:
        obs = step_result.observation
        if len(obs) > 400:
            obs = obs[:400] + f"{_DIM}... ({len(step_result.observation)} chars){_RESET}"
        lines = obs.splitlines()
        if len(lines) == 1:
            print(f"  {icon} {_DIM}{lines[0]}{_RESET}")
        else:
            for i, line in enumerate(lines[:10]):
                prefix = "  │ " if i > 0 else f"  {icon} "
                print(f"{prefix}{_DIM}{line[:120]}{_RESET}")
            if len(lines) > 10:
                print(f"  │ {_DIM}... ({len(lines) - 10} more lines){_RESET}")
    print()


def _on_task_end(report):
    summary = report.get("summary", {})
    steps = summary.get("total_steps", "?")
    success = summary.get("summary", {}).get("success", False)
    status = f"{_GREEN}Success{_RESET}" if success else f"{_RED}Failed{_RESET}"
    print(f"{_BOLD}{'━' * 60}{_RESET}")
    print(f"  {_BOLD}Done.{_RESET} Steps: {steps} | {status}")
    print(f"{_BOLD}{'━' * 60}{_RESET}")


# ═══════════════════════════════════════════════════════
#  Example tool hooks
# ═══════════════════════════════════════════════════════


def build_tool_hooks() -> ToolHookRunner:
    """Build tool hook runner with example hooks."""
    runner = ToolHookRunner()
    runner.register_pre("shell_exec", _warn_dangerous_commands)
    runner.register_post("file_read", _hint_large_output)
    return runner


def _warn_dangerous_commands(tool_name: str, args: Dict) -> Optional[HookDecision]:
    """PreToolUse for shell_exec: warn if command looks destructive."""
    cmd = args.get("command", "")
    dangerous_patterns = ["rm -rf", "rm -r", "mkfs", "dd if=", "> /dev/sd",
                          "chmod -R 777", ":(){ :|:& };:"]

    for pattern in dangerous_patterns:
        if pattern in cmd:
            return HookDecision(
                action=HookAction.INJECT,
                message=(
                    f"[Hook Warning] The command contains '{pattern}' which may be destructive. "
                    "Double-check before proceeding. Consider using a more targeted approach."
                ),
            )
    return None


def _hint_large_output(tool_name: str, args: Dict, result: str) -> Optional[HookDecision]:
    """PostToolUse for file_read: hint at using line ranges for large files."""
    if len(result) > 3000:
        lines = result.count("\n") + 1
        path = args.get("path", "file")
        return HookDecision(
            action=HookAction.INJECT,
            message=(
                f"[Hook Note] Output is {lines} lines. "
                f"For subsequent reads of '{path}', consider using "
                "start_line and end_line parameters to read specific sections."
            ),
        )
    return None
