from nanoharness.core.schema import AgentMessage, EvaluationResult, LLMResponse, StepResult, StopSignal, ToolCall


class TestToolCall:
    def test_create(self):
        tc = ToolCall(name="search", arguments={"query": "hello"})
        assert tc.name == "search"
        assert tc.arguments == {"query": "hello"}

    def test_model_dump(self):
        tc = ToolCall(name="f", arguments={"x": 1})
        d = tc.model_dump()
        assert d == {"name": "f", "arguments": {"x": 1}}


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
