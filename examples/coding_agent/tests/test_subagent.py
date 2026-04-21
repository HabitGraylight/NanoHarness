"""Tests for the subagent system: context, run loop, task tool."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from nanoharness.core.schema import LLMResponse, ToolCall

from app.dispatch import DispatchRegistry, tool_result
from app.subagent import (
    SUBAGENT_TOOL_WHITELIST,
    SubagentContext,
    build_subagent_context,
    register_task_tool,
    run_subagent,
)


# ── Helpers ──


def _make_registry(tmp_path) -> DispatchRegistry:
    """Build a minimal registry with a couple of read-only tools."""
    reg = DispatchRegistry(workspace_root=str(tmp_path))

    def fake_file_read(path: str) -> tool_result:
        return tool_result(ok=True, output=f"content of {path}")

    def fake_search_code(pattern: str, path: str = ".") -> tool_result:
        return tool_result(ok=True, output=f"matches for {pattern}")

    reg.register("file_read", lambda args: fake_file_read(**args),
                 schema={"type": "function", "function": {
                     "name": "file_read", "description": "Read a file",
                     "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
                 }}, path_params=["path"])

    reg.register("search_code", lambda args: fake_search_code(**args),
                 schema={"type": "function", "function": {
                     "name": "search_code", "description": "Search code",
                     "parameters": {"type": "object", "properties": {"pattern": {"type": "string"}}, "required": ["pattern"]},
                 }}, path_params=[])

    # Also register a write tool (should NOT appear in subagent whitelist)
    reg.register("file_write", lambda args: tool_result(ok=True, output="wrote"),
                 schema={"type": "function", "function": {
                     "name": "file_write", "description": "Write a file",
                     "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
                 }}, path_params=["path"])

    return reg


class ImmediateAnswer:
    """LLM that immediately returns a text answer (no tool calls)."""
    def chat(self, messages, tools=None):
        return LLMResponse(content="The file contains a hello world program.", tool_calls=None)


class ToolThenAnswer:
    """LLM that makes one tool call, then returns a summary."""
    def __init__(self):
        self._call_count = 0

    def chat(self, messages, tools=None):
        self._call_count += 1
        if self._call_count == 1:
            return LLMResponse(
                content="Let me read the file.",
                tool_calls=[ToolCall(name="file_read", arguments={"path": "main.py"})],
            )
        return LLMResponse(content="The file contains a hello world function.", tool_calls=None)


class MultiToolThenAnswer:
    """LLM that makes multiple tool calls across turns."""
    def __init__(self):
        self._call_count = 0

    def chat(self, messages, tools=None):
        self._call_count += 1
        if self._call_count == 1:
            return LLMResponse(
                content="Searching first.",
                tool_calls=[ToolCall(name="search_code", arguments={"pattern": "def hello"})],
            )
        if self._call_count == 2:
            return LLMResponse(
                content="Now reading.",
                tool_calls=[ToolCall(name="file_read", arguments={"path": "main.py"})],
            )
        return LLMResponse(content="Found hello() in main.py. It prints 'hello world'.", tool_calls=None)


class NeverStops:
    """LLM that always makes a tool call (for testing max_turns)."""
    def chat(self, messages, tools=None):
        return LLMResponse(
            content="Reading more...",
            tool_calls=[ToolCall(name="file_read", arguments={"path": "a.py"})],
        )


class SummaryOnForce:
    """Like NeverStops, but returns a summary when tools are removed (forced summary)."""
    def __init__(self, max_tool_turns=20):
        self._call_count = 0
        self._max_tool_turns = max_tool_turns

    def chat(self, messages, tools=None):
        self._call_count += 1
        if tools is not None and self._call_count <= self._max_tool_turns:
            return LLMResponse(
                content="Reading...",
                tool_calls=[ToolCall(name="file_read", arguments={"path": "a.py"})],
            )
        return LLMResponse(content="I found some Python files.", tool_calls=None)


# ── SubagentContext tests ──


class TestSubagentContext:
    def test_default_fields(self):
        ctx = SubagentContext()
        assert ctx.messages == []
        assert ctx.tools == {}
        assert ctx.handlers == {}
        assert ctx.max_turns == 8

    def test_custom_max_turns(self):
        ctx = SubagentContext(max_turns=3)
        assert ctx.max_turns == 3


class TestBuildSubagentContext:
    def test_whitelist_filters_tools(self, tmp_path):
        reg = _make_registry(tmp_path)
        ctx = build_subagent_context(reg)

        # Only whitelisted tools should be present
        assert "file_read" in ctx.tools
        assert "search_code" in ctx.tools
        # file_write is NOT in the whitelist
        assert "file_write" not in ctx.tools

    def test_handlers_are_callable(self, tmp_path):
        reg = _make_registry(tmp_path)
        ctx = build_subagent_context(reg)
        result = ctx.handlers["file_read"]({"path": "test.py"})
        assert "test.py" in result

    def test_sandbox_applied_in_handlers(self, tmp_path):
        reg = _make_registry(tmp_path)
        ctx = build_subagent_context(reg)
        # Escaping path should return an error string (not raise)
        result = ctx.handlers["file_read"]({"path": "../../etc/passwd"})
        assert "Error" in result


# ── run_subagent tests ──


class TestRunSubagent:
    def test_immediate_answer(self, tmp_path):
        reg = _make_registry(tmp_path)
        ctx = build_subagent_context(reg)
        result = run_subagent("What is in main.py?", ImmediateAnswer(), ctx)
        assert "hello world" in result

    def test_tool_then_summary(self, tmp_path):
        reg = _make_registry(tmp_path)
        ctx = build_subagent_context(reg)
        llm = ToolThenAnswer()
        result = run_subagent("Read main.py", llm, ctx)
        assert "hello world" in result
        # Verify tool call was made (2 assistant messages: tool call + summary)
        assistant_msgs = [m for m in ctx.messages if m["role"] == "assistant"]
        assert len(assistant_msgs) == 2

    def test_multi_turn_tools(self, tmp_path):
        reg = _make_registry(tmp_path)
        ctx = build_subagent_context(reg)
        llm = MultiToolThenAnswer()
        result = run_subagent("Investigate hello()", llm, ctx)
        assert "hello" in result
        # Should have 3 assistant messages (search, read, summary)
        assistant_msgs = [m for m in ctx.messages if m["role"] == "assistant"]
        assert len(assistant_msgs) == 3

    def test_max_turns_forces_summary(self, tmp_path):
        reg = _make_registry(tmp_path)
        ctx = build_subagent_context(reg, max_turns=3)
        llm = SummaryOnForce(max_tool_turns=20)
        result = run_subagent("Keep reading files", llm, ctx)
        # Should get forced summary, not hang
        assert "Python files" in result
        # Messages should have exactly max_turns assistant + tool pairs
        tool_msgs = [m for m in ctx.messages if m["role"] == "tool"]
        assert len(tool_msgs) == 3

    def test_messages_start_fresh(self, tmp_path):
        """Each run_subagent call resets messages."""
        reg = _make_registry(tmp_path)
        ctx = build_subagent_context(reg)
        run_subagent("First task", ImmediateAnswer(), ctx)
        first_count = len(ctx.messages)
        run_subagent("Second task", ImmediateAnswer(), ctx)
        second_count = len(ctx.messages)
        # Second run should NOT append to first run's messages
        assert second_count == first_count

    def test_unknown_tool_returns_error(self, tmp_path):
        """Subagent handles unknown tool names gracefully."""
        class CallBadTool:
            def chat(self, messages, tools=None):
                if not any(m.get("tool_calls") for m in messages if m["role"] == "assistant"):
                    return LLMResponse(
                        content="Using bad tool",
                        tool_calls=[ToolCall(name="nonexistent_tool", arguments={})],
                    )
                return LLMResponse(content="Done anyway.", tool_calls=None)

        reg = _make_registry(tmp_path)
        ctx = build_subagent_context(reg)
        result = run_subagent("Use a bad tool", CallBadTool(), ctx)
        assert "Done" in result
        # Error should be in messages
        tool_msgs = [m for m in ctx.messages if m["role"] == "tool"]
        assert any("Unknown tool" in m["content"] for m in tool_msgs)

    def test_empty_prompt(self, tmp_path):
        """Empty prompt still runs (LLM decides what to do)."""
        reg = _make_registry(tmp_path)
        ctx = build_subagent_context(reg)
        result = run_subagent("", ImmediateAnswer(), ctx)
        assert result  # non-empty summary


# ── Task tool (integration with dispatch registry) ──


class TestTaskTool:
    def test_task_tool_registered(self, tmp_path):
        reg = _make_registry(tmp_path)
        register_task_tool(registry=reg, llm_client=ImmediateAnswer())
        assert "task" in reg.dispatch_map
        schemas = reg.get_tool_schemas()
        task_schema = next(s for s in schemas if s["function"]["name"] == "task")
        assert "description" in task_schema["function"]["parameters"]["properties"]

    def test_task_tool_runs_subagent(self, tmp_path):
        reg = _make_registry(tmp_path)
        register_task_tool(registry=reg, llm_client=ImmediateAnswer())
        result = reg.call("task", {"description": "What is in main.py?"})
        assert "hello world" in result

    def test_task_tool_empty_description_fails(self, tmp_path):
        reg = _make_registry(tmp_path)
        register_task_tool(registry=reg, llm_client=ImmediateAnswer())
        with pytest.raises(RuntimeError, match="No task description"):
            reg.call("task", {"description": ""})

    def test_task_tool_with_tool_call_subagent(self, tmp_path):
        """Subagent inside task tool can use tools and return summary."""
        reg = _make_registry(tmp_path)
        register_task_tool(registry=reg, llm_client=ToolThenAnswer())
        result = reg.call("task", {"description": "Read main.py"})
        assert "hello world" in result

    def test_task_tool_no_path_validation(self, tmp_path):
        """task tool has no path params (description is not a path)."""
        reg = _make_registry(tmp_path)
        register_task_tool(registry=reg, llm_client=ImmediateAnswer())
        assert reg._path_params.get("task", []) == []


# ── Whitelist correctness ──


class TestWhitelist:
    def test_whitelist_is_read_only(self):
        """No write tools in the subagent whitelist."""
        write_tools = {"file_write", "file_edit", "git_commit", "git_push",
                       "git_reset", "git_revert", "shell_exec", "git_add",
                       "git_checkout", "git_merge", "git_init", "git_stash",
                       "git_stash_pop", "git_branch_create"}
        assert len(SUBAGENT_TOOL_WHITELIST & write_tools) == 0
