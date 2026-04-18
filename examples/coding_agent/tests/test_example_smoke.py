"""Smoke tests for the coding agent example.

These verify that the example can be assembled and run without errors,
using a mock LLM so no real API calls are needed.

Run from examples/coding_agent/:
    PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest tests/ -v
"""

import sys
import os

# Ensure example directory is on sys.path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from nanoharness.components.context.simple_context import SimpleContextManager
from nanoharness.components.evaluator.trace_evaluator import TraceEvaluator
from nanoharness.components.hooks.simple_hooks import SimpleHookManager
from nanoharness.components.memory.simple_memory import SimpleMemoryManager
from nanoharness.components.state.json_store import JsonStateStore
from nanoharness.core.engine import NanoEngine
from nanoharness.core.prompt import PromptManager
from nanoharness.core.schema import LLMResponse, PermissionLevel


class MockLLMClient:
    """Stub LLM that returns a canned response with no tool calls."""

    def __init__(self, response: str = "Done."):
        self._response = response

    def chat(self, messages, tools=None) -> LLMResponse:
        return LLMResponse(content=self._response, tool_calls=None)


# ── Wiring smoke tests ──


def test_tools_load():
    """All shell + Python tools register successfully."""
    from app.tools import build_tools

    tools = build_tools()
    schemas = tools.get_tool_schemas()
    assert len(schemas) >= 20
    names = [s["function"]["name"] for s in schemas]
    assert "file_read" in names
    assert "file_edit" in names
    assert "search_code" in names
    assert "list_files" in names


def test_permissions_policy():
    """Permission levels match coding agent policy."""
    from app.permissions import build_permissions

    perms = build_permissions()
    assert perms.check("git_reset", {}) == PermissionLevel.DENY
    assert perms.check("git_revert", {}) == PermissionLevel.DENY
    assert perms.check("git_push", {}) == PermissionLevel.CONFIRM
    assert perms.check("shell_exec", {}) == PermissionLevel.CONFIRM
    assert perms.check("file_read", {}) == PermissionLevel.ALLOW
    assert perms.check("file_edit", {}) == PermissionLevel.ALLOW
    assert perms.check("git_status", {}) == PermissionLevel.ALLOW


def test_prompts_load():
    """Coding-agent-specific prompts load from app/prompts.yaml."""
    pm = PromptManager.from_file("app/prompts.yaml")
    assert "system.coding_agent" in pm.keys()
    assert "software engineer" in pm.get("system.coding_agent").lower()


def test_hooks_build():
    """Hook manager assembles without error."""
    from app.hooks import build_hooks

    hooks = build_hooks()
    # Should not raise
    hooks.trigger("on_task_start", "test query")


# ── Engine run smoke test ──


def test_engine_runs_with_mock_llm(tmp_path):
    """Full engine assembly runs to completion with a mock LLM."""
    from app.tools import build_tools
    from app.permissions import build_permissions
    from app.hooks import build_hooks

    llm = MockLLMClient(response="I have completed the task.")
    tools = build_tools()
    perms = build_permissions()
    hooks = build_hooks()

    prompts = PromptManager.from_file("app/prompts.yaml")
    memory = SimpleMemoryManager(
        persist_path=str(tmp_path / "memory.json"), prompts=prompts
    )

    engine = NanoEngine(
        llm_client=llm,
        tools=tools,
        context=SimpleContextManager(system_prompt="Test prompt."),
        state=JsonStateStore(str(tmp_path / "state.json")),
        hooks=hooks,
        evaluator=TraceEvaluator(),
        permissions=perms,
        memory=memory,
        prompts=prompts,
        max_steps=5,
    )

    report = engine.run("Do something simple.")

    assert report["summary"]["total_steps"] == 1
    assert report["trajectory"][0]["status"] == "terminated"
    assert report["summary"]["success"] is True


def test_engine_with_tool_call(tmp_path):
    """Engine dispatches a tool call and returns observation."""
    from app.tools import build_tools
    from app.permissions import build_permissions
    from nanoharness.core.schema import ToolCall

    tools = build_tools()
    call_count = 0

    class ToolThenDone:
        """Returns one tool call, then terminates."""

        def chat(self, messages, tools=None):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return LLMResponse(
                    content="Let me list the files.",
                    tool_calls=[
                        ToolCall(name="list_files", arguments={"pattern": "*.py", "path": "."})
                    ],
                )
            return LLMResponse(content="I found the files.", tool_calls=None)

    prompts = PromptManager.from_file("app/prompts.yaml")
    engine = NanoEngine(
        llm_client=ToolThenDone(),
        tools=tools,
        context=SimpleContextManager(system_prompt="Test"),
        state=JsonStateStore(str(tmp_path / "state.json")),
        hooks=SimpleHookManager(),
        evaluator=TraceEvaluator(),
        permissions=build_permissions(),
        memory=SimpleMemoryManager(
            persist_path=str(tmp_path / "memory.json"), prompts=prompts
        ),
        prompts=prompts,
        max_steps=5,
    )

    report = engine.run("List Python files.")

    assert report["summary"]["total_steps"] == 2
    assert report["trajectory"][0]["status"] == "success"
    assert ".py" in report["trajectory"][0]["observation"]
