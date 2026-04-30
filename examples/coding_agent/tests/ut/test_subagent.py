"""Tests for the subagent system: context, whitelist."""

import pytest

from nanoharness.core.schema import LLMResponse, ToolCall

from app.dispatch import DispatchRegistry, tool_result
from app.subagent import (
    SUBAGENT_TOOL_WHITELIST,
    SubagentContext,
    build_subagent_context,
)


# -- Helpers --


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


# -- SubagentContext tests --


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


# -- Whitelist correctness --


class TestWhitelist:
    def test_whitelist_is_read_only(self):
        """No write tools in the subagent whitelist."""
        write_tools = {"file_write", "file_edit", "git_commit", "git_push",
                       "git_reset", "git_revert", "shell_exec", "git_add",
                       "git_checkout", "git_merge", "git_init", "git_stash",
                       "git_stash_pop", "git_branch_create"}
        assert len(SUBAGENT_TOOL_WHITELIST & write_tools) == 0
