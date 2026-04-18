"""Smoke tests for the coding agent example.

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


# ── Component tests ──


def test_tools_load():
    """All shell + Python tools register successfully."""
    from app.tools import build_tools

    scripts_dir = os.path.join(os.path.dirname(__file__), "..", "configs", "scripts")
    tools = build_tools(scripts_dir=scripts_dir)
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


def test_permissions_enforce():
    """enforce() returns correct error messages."""
    from app.permissions import build_permissions

    perms = build_permissions()
    assert perms.enforce("git_reset", {}) is not None
    assert "denied" in perms.enforce("git_reset", {}).lower()
    assert perms.enforce("file_read", {}) is None


def test_prompts_load():
    """Coding-agent-specific prompts load from app/prompts.yaml."""
    prompts_path = os.path.join(os.path.dirname(__file__), "..", "app", "prompts.yaml")
    pm = PromptManager.from_file(prompts_path)
    assert "system.coding_agent" in pm.keys()
    assert "software engineer" in pm.get("system.coding_agent").lower()


def test_hooks_build():
    """Hook manager assembles without error."""
    from app.hooks import build_hooks

    hooks = build_hooks()
    hooks.trigger("on_task_start", "test query")


# ── Engine assembly tests ──


def test_engine_runs_with_mock_llm(tmp_path):
    """Engine runs to completion with a mock LLM."""
    llm = MockLLMClient(response="I have completed the task.")

    engine = NanoEngine(
        llm_client=llm,
        tools=_build_test_tools(),
        context=SimpleContextManager(system_prompt="Test."),
        state=JsonStateStore(str(tmp_path / "state.json")),
        hooks=SimpleHookManager(),
        evaluator=TraceEvaluator(),
        permissions=_build_test_perms(),
        max_steps=5,
    )

    report = engine.run("Do something simple.")
    assert report["summary"]["total_steps"] == 1
    assert report["trajectory"][0]["status"] == "terminated"


def test_engine_with_tool_call(tmp_path):
    """Engine dispatches a tool call and returns observation."""
    from nanoharness.core.schema import ToolCall

    call_count = 0

    class ToolThenDone:
        def chat(self, messages, tools=None):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return LLMResponse(
                    content="Let me list files.",
                    tool_calls=[ToolCall(name="list_files", arguments={"pattern": "*.py", "path": "."})],
                )
            return LLMResponse(content="Done.", tool_calls=None)

    engine = NanoEngine(
        llm_client=ToolThenDone(),
        tools=_build_test_tools(),
        context=SimpleContextManager(system_prompt="Test"),
        state=JsonStateStore(str(tmp_path / "state.json")),
        hooks=SimpleHookManager(),
        evaluator=TraceEvaluator(),
        permissions=_build_test_perms(),
        max_steps=5,
    )

    report = engine.run("List Python files.")
    assert report["summary"]["total_steps"] == 2
    assert ".py" in report["trajectory"][0]["observation"]


def test_builder_assembles(tmp_path, monkeypatch):
    """build_coding_engine() wires everything correctly."""
    from unittest.mock import patch
    from app.builder import build_coding_engine, SANDBOX

    mock_llm = MockLLMClient(response="Task complete.")

    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")

    with patch("app.builder.OpenAIAdapter", return_value=mock_llm):
        engine = build_coding_engine()

    assert isinstance(engine, NanoEngine)
    report = engine.run("Test task")
    assert report["summary"]["total_steps"] >= 1


# ── UI test ──


def test_ui_banner():
    """UI module loads and has expected constants."""
    from app.ui import BANNER, HELP_TEXT
    assert "Coding Agent" in BANNER
    assert "/quit" in HELP_TEXT


# ── Helpers ──


def _build_test_tools():
    from app.tools import build_tools
    scripts_dir = os.path.join(os.path.dirname(__file__), "..", "configs", "scripts")
    return build_tools(scripts_dir=scripts_dir)


def _build_test_perms():
    from app.permissions import build_permissions
    return build_permissions()
