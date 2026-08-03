from nanoharness.core.schema import (
    AgentMessage,
    CORE_PROTOCOL_VERSION,
    EventType,
    EvaluationResult,
    HarnessEvent,
    LLMResponse,
    RunCheckpoint,
    RunContext,
    RunStatus,
    StepResult,
    StopSignal,
    ToolCall,
    ToolExecution,
    ToolExecutionStatus,
)


class TestToolCall:
    def test_create(self):
        tc = ToolCall(name="search", arguments={"query": "hello"})
        assert tc.name == "search"
        assert tc.arguments == {"query": "hello"}

    def test_model_dump(self):
        tc = ToolCall(name="f", arguments={"x": 1})
        d = tc.model_dump()
        assert d == {"name": "f", "arguments": {"x": 1}}

    def test_call_id_is_serialized_when_present(self):
        tc = ToolCall(name="f", arguments={}, call_id="call_123")
        assert tc.model_dump()["call_id"] == "call_123"


class TestLLMResponse:
    def test_no_tool_calls(self):
        r = LLMResponse(content="hi")
        assert r.content == "hi"
        assert r.tool_calls is None

    def test_with_tool_calls(self):
        tc = ToolCall(name="f", arguments={})
        r = LLMResponse(content="", tool_calls=[tc])
        assert len(r.tool_calls) == 1


class TestAgentMessage:
    def test_basic(self):
        msg = AgentMessage(role="user", content="hello")
        assert msg.role == "user"
        assert msg.tool_calls is None

    def test_with_tool_calls(self):
        tc = ToolCall(name="f", arguments={"a": 1})
        msg = AgentMessage(role="assistant", content="", tool_calls=[tc])
        dumped = msg.model_dump()
        assert dumped["tool_calls"][0]["name"] == "f"


class TestStepResult:
    def test_defaults(self):
        s = StepResult(step_id=0, thought="thinking")
        assert s.status == "success"
        assert s.action is None
        assert s.observation is None

    def test_custom_status(self):
        s = StepResult(step_id=1, thought="", status="error")
        assert s.status == "error"

    def test_stop_signal_field(self):
        s = StepResult(step_id=0, thought="t")
        assert s.stop_signal is None

    def test_with_stop_signal(self):
        sig = StopSignal(should_stop=True, reason="spinning", stop_category="error_loop")
        s = StepResult(step_id=0, thought="t", stop_signal=sig)
        assert s.stop_signal.should_stop is True

    def test_actions_capture_complete_tool_trace(self):
        execution = ToolExecution(
            call_id="call_1",
            name="read",
            arguments={"path": "x.py"},
            status=ToolExecutionStatus.SUCCESS,
            output="content",
        )
        s = StepResult(step_id=0, thought="read", actions=[execution])
        assert s.actions[0].call_id == "call_1"
        assert s.actions[0].output == "content"


class TestStopSignal:
    def test_defaults(self):
        s = StopSignal()
        assert s.should_stop is False
        assert s.reason == ""
        assert s.stop_category == ""

    def test_custom(self):
        s = StopSignal(should_stop=True, reason="3 consecutive errors", stop_category="error_loop")
        assert s.should_stop is True
        assert "3 consecutive" in s.reason


class TestEvaluationResult:
    def test_defaults(self):
        r = EvaluationResult()
        assert r.achieved is False
        assert r.confidence == 0.0
        assert r.explanation == ""
        assert r.evidence == []

    def test_custom(self):
        r = EvaluationResult(achieved=True, confidence=0.9, explanation="Done", evidence=["obs1"])
        assert r.achieved is True
        assert len(r.evidence) == 1


class TestRunProtocol:
    def test_run_context_has_stable_identity_and_version(self):
        run = RunContext(query="fix it", max_steps=5)
        assert run.run_id.startswith("run_")
        assert run.session_id.startswith("session_")
        assert run.protocol_version == CORE_PROTOCOL_VERSION

    def test_checkpoint_serializes_as_json(self):
        checkpoint = RunCheckpoint(
            run_id="run_1",
            session_id="session_1",
            query="fix it",
            status=RunStatus.COMPLETED,
        )
        dumped = checkpoint.model_dump(mode="json")
        assert dumped["status"] == "completed"
        assert isinstance(dumped["updated_at"], str)

    def test_harness_event_is_ordered_and_typed(self):
        event = HarnessEvent(
            run_id="run_1",
            session_id="session_1",
            sequence=3,
            type=EventType.TOOL_COMPLETED,
        )
        assert event.sequence == 3
        assert event.type == "tool_completed"
