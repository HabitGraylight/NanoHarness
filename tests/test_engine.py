from nanoharness.components.context.simple_context import SimpleContextManager
from nanoharness.components.evaluator.trace_evaluator import TraceEvaluator
from nanoharness.components.hooks.simple_hooks import SimpleHookManager
from nanoharness.components.state.json_store import JsonStateStore
from nanoharness.components.tools.dict_registry import DictToolRegistry
from nanoharness.core.engine import NanoEngine
from nanoharness.core.schema import LLMResponse, ToolCall


class TestEngineBasicLoop:
    def test_immediate_terminate(self, mock_llm):
        llm = mock_llm([LLMResponse(content="done")])
        engine = NanoEngine(
            llm_client=llm,
            tools=DictToolRegistry(),
            context=SimpleContextManager(),
            state=JsonStateStore("/tmp/test_state.json"),
            hooks=SimpleHookManager(),
            evaluator=TraceEvaluator(),
        )
        report = engine.run("hello")
        assert report["summary"]["total_steps"] == 1
        assert report["summary"]["success"] is True

    def test_tool_call_then_terminate(self, mock_llm):
        reg = DictToolRegistry()

        @reg.tool
        def echo(text: str):
            """Echo back."""
            return text

        llm = mock_llm([
            LLMResponse(
                content="calling echo",
                tool_calls=[ToolCall(name="echo", arguments={"text": "hi"})],
            ),
            LLMResponse(content="all done"),
        ])
        engine = NanoEngine(
            llm_client=llm,
            tools=reg,
            context=SimpleContextManager(),
            state=JsonStateStore("/tmp/test_state2.json"),
            hooks=SimpleHookManager(),
            evaluator=TraceEvaluator(),
        )
        report = engine.run("echo hi")
        assert report["summary"]["total_steps"] == 2
        traj = report["trajectory"]
        assert traj[0]["observation"] == "hi"
        assert traj[0]["status"] == "success"

    def test_tool_error(self, mock_llm):
        reg = DictToolRegistry()

        @reg.tool
        def fail():
            """Always fails."""
            raise ValueError("boom")

        llm = mock_llm([
            LLMResponse(
                content="trying",
                tool_calls=[ToolCall(name="fail", arguments={})],
            ),
            LLMResponse(content="done"),
        ])
        engine = NanoEngine(
            llm_client=llm,
            tools=reg,
            context=SimpleContextManager(),
            state=JsonStateStore("/tmp/test_state3.json"),
            hooks=SimpleHookManager(),
            evaluator=TraceEvaluator(),
        )
        report = engine.run("do it")
        assert report["trajectory"][0]["status"] == "error"
        assert "ToolError(fail)" in report["trajectory"][0]["observation"]


class TestEngineHooks:
    def test_hooks_triggered(self, mock_llm):
        llm = mock_llm([LLMResponse(content="done")])
        hooks = SimpleHookManager()
        stages = []
        for stage in ["on_task_start", "on_thought_ready", "on_step_end", "on_task_end"]:
            hooks.register(stage, lambda d, s=stage: stages.append(s))

        engine = NanoEngine(
            llm_client=llm,
            tools=DictToolRegistry(),
            context=SimpleContextManager(),
            state=JsonStateStore("/tmp/test_hooks.json"),
            hooks=hooks,
            evaluator=TraceEvaluator(),
        )
        engine.run("test")
        assert stages == ["on_task_start", "on_thought_ready", "on_step_end", "on_task_end"]


class TestEngineContext:
    def test_context_messages(self, mock_llm):
        llm = mock_llm([LLMResponse(content="hello")])
        ctx = SimpleContextManager(system_prompt="be helpful")
        engine = NanoEngine(
            llm_client=llm,
            tools=DictToolRegistry(),
            context=ctx,
            state=JsonStateStore("/tmp/test_ctx.json"),
            hooks=SimpleHookManager(),
            evaluator=TraceEvaluator(),
        )
        engine.run("hi")
        msgs = ctx.get_full_context()
        assert msgs[0]["role"] == "system"
        assert msgs[1]["role"] == "user"
        assert msgs[2]["role"] == "assistant"
