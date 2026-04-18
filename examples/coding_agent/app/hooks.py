"""Coding agent lifecycle hooks.

Provides step-by-step output to give the user visibility into
what the agent is thinking and doing.
"""

from nanoharness.components.hooks.simple_hooks import SimpleHookManager
from nanoharness.core.base import HookStage


def build_hooks() -> SimpleHookManager:
    """Build the coding agent hook manager with default output hooks."""
    hooks = SimpleHookManager()

    hooks.register(HookStage.ON_TASK_START, _on_task_start)
    hooks.register(HookStage.ON_THOUGHT_READY, _on_thought)
    hooks.register(HookStage.ON_STEP_END, _on_step_end)
    hooks.register(HookStage.ON_TASK_END, _on_task_end)

    return hooks


def _on_task_start(query):
    print(f"\n{'='*60}")
    print(f" Task: {query}")
    print(f"{'='*60}\n")


def _on_thought(response):
    if response.content:
        print(f"  Thinking: {response.content[:200]}")
    if response.tool_calls:
        for tc in response.tool_calls:
            args_summary = str(tc.arguments)[:100]
            print(f"  -> {tc.name}({args_summary})")


def _on_step_end(step_result):
    status_icon = {"success": "+", "error": "x", "terminated": "."}.get(
        step_result.status, "?"
    )
    if step_result.observation:
        obs_preview = step_result.observation[:150]
        print(f"  [{status_icon}] {obs_preview}")
    print()


def _on_task_end(report):
    summary = report.get("summary", {})
    print(f"\n{'='*60}")
    print(f" Done. Steps: {summary.get('total_steps', '?')} | "
          f"Success: {summary.get('success', '?')}")
    print(f"{'='*60}")
