"""ST for hook system — engine integration with tool hooks."""

import pytest

from nanoharness.core.schema import AgentMessage, LLMResponse, ToolCall
from app.hooks import HookAction, HookDecision, ToolHookRunner


class TestEngineWithToolHooks:
    def test_pre_hook_block(self, tmp_path):
        """PreToolUse BLOCK prevents tool execution."""
        from nanoharness.components.context.simple_context import SimpleContextManager
        from nanoharness.components.evaluator.trace_evaluator import TraceEvaluator
        from nanoharness.components.hooks.simple_hooks import SimpleHookManager
        from nanoharness.components.state.json_store import JsonStateStore
        from nanoharness.core.engine import NanoEngine
        from app.dispatch import DispatchRegistry, tool_result

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

        hooks = ToolHookRunner()
        hooks.register_pre("danger", lambda n, a: HookDecision(HookAction.BLOCK, "Blocked!"))

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
        assert len(tool_called) == 0
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
