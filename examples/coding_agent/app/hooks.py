"""Coding agent lifecycle hooks with terminal UI formatting."""

from nanoharness.components.hooks.simple_hooks import SimpleHookManager
from nanoharness.core.base import HookStage

# ANSI color codes
_BOLD = "\033[1m"
_DIM = "\033[2m"
_CYAN = "\033[36m"
_GREEN = "\033[32m"
_YELLOW = "\033[33m"
_RED = "\033[31m"
_BLUE = "\033[34m"
_RESET = "\033[0m"


def build_hooks() -> SimpleHookManager:
    """Build hook manager with formatted terminal output."""
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
    # Show thinking
    if response.content:
        content = response.content.strip()
        # Truncate very long thoughts
        if len(content) > 500:
            content = content[:500] + f"{_DIM}... ({len(response.content)} chars){_RESET}"
        print(f"  {_DIM}Thinking:{_RESET} {content}")

    # Show tool calls
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
        # For observations, show more but still cap
        if len(obs) > 400:
            obs = obs[:400] + f"{_DIM}... ({len(step_result.observation)} chars){_RESET}"
        # Indent multi-line observations
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
    success = summary.get("success", False)
    status = f"{_GREEN}Success{_RESET}" if success else f"{_RED}Failed{_RESET}"
    print(f"{_BOLD}{'━' * 60}{_RESET}")
    print(f"  {_BOLD}Done.{_RESET} Steps: {steps} | {status}")
    print(f"{_BOLD}{'━' * 60}{_RESET}")
