"""Tests for tool hook runner: pre/post interception pipeline."""

import pytest

from nanoharness.core.schema import LLMResponse, ToolCall

from app.hooks import HookAction, HookDecision, ToolHookRunner, build_tool_hooks


class TestHookDecision:
    def test_continue(self):
        d = HookDecision(action=HookAction.CONTINUE)
        assert d.action == 0
        assert d.message is None

    def test_block_with_message(self):
        d = HookDecision(action=HookAction.BLOCK, message="Nope")
        assert d.action == 1
        assert d.message == "Nope"

    def test_inject(self):
        d = HookDecision(action=HookAction.INJECT, message="Heads up")
        assert d.action == 2
        assert d.message == "Heads up"


class TestToolHookRunner:
    def test_no_hooks_returns_none(self):
        runner = ToolHookRunner()
        assert runner.run_pre("any_tool", {}) is None
        assert runner.run_post("any_tool", {}, "result") is None

    def test_pre_hook_continue(self):
        runner = ToolHookRunner()
        runner.register_pre("*", lambda name, args: None)
        assert runner.run_pre("tool", {}) is None

    def test_pre_hook_block(self):
        runner = ToolHookRunner()
        runner.register_pre("dangerous", lambda n, a: HookDecision(HookAction.BLOCK, "Blocked!"))
        result = runner.run_pre("dangerous", {})
        assert result.action == HookAction.BLOCK
        assert result.message == "Blocked!"

    def test_pre_hook_inject(self):
        runner = ToolHookRunner()
        runner.register_pre("shell_exec", lambda n, a: HookDecision(HookAction.INJECT, "Be careful"))
        result = runner.run_pre("shell_exec", {"command": "ls"})
        assert result.action == HookAction.INJECT
        assert "Be careful" in result.message

    def test_glob_matching(self):
        runner = ToolHookRunner()
        runner.register_pre("git_*", lambda n, a: HookDecision(HookAction.BLOCK, "No git"))
        assert runner.run_pre("git_push", {}) is not None
        assert runner.run_pre("git_reset", {}) is not None
        assert runner.run_pre("file_read", {}) is None

    def test_first_match_wins(self):
        runner = ToolHookRunner()
        runner.register_pre("*", lambda n, a: HookDecision(HookAction.BLOCK, "First"))
        runner.register_pre("*", lambda n, a: HookDecision(HookAction.INJECT, "Second"))
        result = runner.run_pre("tool", {})
        assert result.action == HookAction.BLOCK

    def test_post_hook_inject(self):
        runner = ToolHookRunner()
        runner.register_post("file_read", lambda n, a, r: HookDecision(HookAction.INJECT, "Large file"))
        result = runner.run_post("file_read", {}, "content" * 1000)
        assert result.action == HookAction.INJECT
        assert "Large file" in result.message

    def test_post_hook_no_match(self):
        runner = ToolHookRunner()
        runner.register_post("file_read", lambda n, a, r: HookDecision(HookAction.INJECT, "X"))
        assert runner.run_post("git_status", {}, "ok") is None

    def test_reset(self):
        runner = ToolHookRunner()
        runner.register_pre("*", lambda n, a: HookDecision(HookAction.BLOCK))
        runner.register_post("*", lambda n, a, r: HookDecision(HookAction.INJECT, "X"))
        runner.reset()
        assert runner.run_pre("tool", {}) is None
        assert runner.run_post("tool", {}, "r") is None


class TestExampleHooks:
    def test_shell_exec_warn_dangerous(self):
        runner = build_tool_hooks()
        result = runner.run_pre("shell_exec", {"command": "rm -rf /tmp/test"})
        assert result is not None
        assert result.action == HookAction.INJECT
        assert "destructive" in result.message.lower()

    def test_shell_exec_pass_safe(self):
        runner = build_tool_hooks()
        result = runner.run_pre("shell_exec", {"command": "ls -la"})
        assert result is None

    def test_file_read_hint_large(self):
        runner = build_tool_hooks()
        large_output = "line\n" * 2000
        result = runner.run_post("file_read", {"path": "big.py"}, large_output)
        assert result is not None
        assert result.action == HookAction.INJECT
        assert "start_line" in result.message

    def test_file_read_no_hint_small(self):
        runner = build_tool_hooks()
        result = runner.run_post("file_read", {"path": "small.py"}, "just a few lines")
        assert result is None

    def test_other_tools_no_hooks(self):
        runner = build_tool_hooks()
        assert runner.run_pre("file_write", {"path": "x"}) is None
        assert runner.run_pre("git_status", {}) is None
        assert runner.run_post("git_log", {}, "output") is None
