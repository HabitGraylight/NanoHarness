"""Tests for tool hook runner: pre/post interception pipeline."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from nanoharness.core.schema import AgentMessage, LLMResponse, ToolCall

from app.hooks import HookAction, HookDecision, ToolHookRunner, build_tool_hooks


# ── HookDecision ──


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


# ── ToolHookRunner ──


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
        assert result.action == HookAction.BLOCK  # first wins

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


# ── Example hooks ──


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


# ── Engine integration (via mock) ──


class TestEngineWithToolHooks:
    def test_pre_hook_block(self, tmp_path):
        """PreToolUse BLOCK prevents tool execution."""
        from nanoharness.components.context.simple_context import SimpleContextManager
        from nanoharness.components.evaluator.trace_evaluator import TraceEvaluator
        from nanoharness.components.hooks.simple_hooks import SimpleHookManager
        from nanoharness.components.state.json_store import JsonStateStore
        from nanoharness.core.engine import NanoEngine
        from app.dispatch import DispatchRegistry, tool_result

        # Registry with one tool
        reg = DispatchRegistry(workspace_root=str(tmp_path))
        tool_called = []

        def my_tool(args):
            tool_called.append(True)
            return tool_result(ok=True, output="should not reach")

        reg.register("danger", lambda a: my_tool(), schema={
            "type": "function", "function": {
                "name": "danger", "description": "D",
                "parameters": {"type": "object", "properties": {}},
            },
        })

        # Hook that blocks
        hooks = ToolHookRunner()
        hooks.register_pre("danger", lambda n, a: HookDecision(HookAction.BLOCK, "Blocked!"))

        # LLM that calls the tool
        call_count = 0

        class CallTool:
            def chat(self, messages, tools=None):
                nonlocal call_count
                call_count += 1
                if call_count == 1:
                    return LLMResponse(
                        content="Calling danger",
                        tool_calls=[ToolCall(name="danger", arguments={})],
                    )
                return LLMResponse(content="Done", tool_calls=None)

        engine = NanoEngine(
            llm_client=CallTool(),
            tools=reg,
            context=SimpleContextManager(system_prompt="Test"),
            state=JsonStateStore(str(tmp_path / "state.json")),
            hooks=SimpleHookManager(),
            evaluator=TraceEvaluator(),
            tool_hooks=hooks,
        )

        report = engine.run("Do it")
        # Tool should NOT have been called
        assert len(tool_called) == 0
        # Observation should contain the block message
        assert "Blocked!" in report["trajectory"][0]["observation"]

    def test_pre_hook_inject(self, tmp_path):
        """PreToolUse INJECT adds a system message before tool runs."""
        from nanoharness.components.context.simple_context import SimpleContextManager
        from nanoharness.components.evaluator.trace_evaluator import TraceEvaluator
        from nanoharness.components.hooks.simple_hooks import SimpleHookManager
        from nanoharness.components.state.json_store import JsonStateStore
        from nanoharness.core.engine import NanoEngine
        from app.dispatch import DispatchRegistry, tool_result

        reg = DispatchRegistry(workspace_root=str(tmp_path))
        reg.register("safe", lambda a: tool_result(ok=True, output="ok"), schema={
            "type": "function", "function": {
                "name": "safe", "description": "S",
                "parameters": {"type": "object", "properties": {}},
            },
        })

        hooks = ToolHookRunner()
        hooks.register_pre("safe", lambda n, a: HookDecision(HookAction.INJECT, "Extra context"))

        call_count = 0

        class CallTool:
            def chat(self, messages, tools=None):
                nonlocal call_count
                call_count += 1
                if call_count == 1:
                    return LLMResponse(
                        content="Calling safe",
                        tool_calls=[ToolCall(name="safe", arguments={})],
                    )
                return LLMResponse(content="Done", tool_calls=None)

        ctx = SimpleContextManager(system_prompt="Test")
        engine = NanoEngine(
            llm_client=CallTool(),
            tools=reg,
            context=ctx,
            state=JsonStateStore(str(tmp_path / "state.json")),
            hooks=SimpleHookManager(),
            evaluator=TraceEvaluator(),
            tool_hooks=hooks,
        )

        engine.run("Do it")
        # The inject message should be in context
        system_msgs = [m for m in ctx._messages if m.role == "system" and "Extra context" in m.content]
        assert len(system_msgs) == 1

    def test_post_hook_inject(self, tmp_path):
        """PostToolUse INJECT appends to the observation."""
        from nanoharness.components.context.simple_context import SimpleContextManager
        from nanoharness.components.evaluator.trace_evaluator import TraceEvaluator
        from nanoharness.components.hooks.simple_hooks import SimpleHookManager
        from nanoharness.components.state.json_store import JsonStateStore
        from nanoharness.core.engine import NanoEngine
        from app.dispatch import DispatchRegistry, tool_result

        reg = DispatchRegistry(workspace_root=str(tmp_path))
        reg.register("reader", lambda a: tool_result(ok=True, output="file content here"), schema={
            "type": "function", "function": {
                "name": "reader", "description": "R",
                "parameters": {"type": "object", "properties": {}},
            },
        })

        hooks = ToolHookRunner()
        hooks.register_post("reader", lambda n, a, r: HookDecision(HookAction.INJECT, "Note: big file"))

        call_count = 0

        class CallTool:
            def chat(self, messages, tools=None):
                nonlocal call_count
                call_count += 1
                if call_count == 1:
                    return LLMResponse(
                        content="Reading",
                        tool_calls=[ToolCall(name="reader", arguments={})],
                    )
                return LLMResponse(content="Done", tool_calls=None)

        ctx = SimpleContextManager(system_prompt="Test")
        engine = NanoEngine(
            llm_client=CallTool(),
            tools=reg,
            context=ctx,
            state=JsonStateStore(str(tmp_path / "state.json")),
            hooks=SimpleHookManager(),
            evaluator=TraceEvaluator(),
            tool_hooks=hooks,
        )

        report = engine.run("Read")
        # Observation should include both the tool output and the hook note
        obs = report["trajectory"][0]["observation"]
        assert "file content here" in obs
        assert "Note: big file" in obs

    def test_no_tool_hooks_works(self, tmp_path):
        """Engine works fine without tool_hooks (backward compatible)."""
        from nanoharness.components.context.simple_context import SimpleContextManager
        from nanoharness.components.evaluator.trace_evaluator import TraceEvaluator
        from nanoharness.components.hooks.simple_hooks import SimpleHookManager
        from nanoharness.components.state.json_store import JsonStateStore
        from nanoharness.core.engine import NanoEngine
        from app.dispatch import DispatchRegistry

        class DoneLLM:
            def chat(self, messages, tools=None):
                return LLMResponse(content="Done", tool_calls=None)

        engine = NanoEngine(
            llm_client=DoneLLM(),
            tools=DispatchRegistry(workspace_root=str(tmp_path)),
            context=SimpleContextManager(system_prompt="Test"),
            state=JsonStateStore(str(tmp_path / "state.json")),
            hooks=SimpleHookManager(),
            evaluator=TraceEvaluator(),
        )

        report = engine.run("Hi")
        assert report["summary"]["total_steps"] == 1
