"""Tests for CodingAgentEvaluator -- mid-loop detection and goal verification."""

from nanoharness.core.schema import LLMResponse, StepResult

from app.coding_evaluator import CodingAgentEvaluator


class FakeLLM:
    def __init__(self, response="ACHIEVED: Task completed."):
        self._response = response

    def chat(self, messages, tools=None):
        return LLMResponse(content=self._response, tool_calls=None)


# ── Error loop detection ──


class TestErrorLoopDetection:
    def test_stops_on_consecutive_errors(self):
        ev = CodingAgentEvaluator(max_consecutive_errors=3)
        trajectory = [
            StepResult(step_id=i, thought="t", status="error")
            for i in range(3)
        ]
        signal = ev.should_stop(trajectory)
        assert signal.should_stop is True
        assert signal.stop_category == "error_loop"
        assert "3 consecutive" in signal.reason

    def test_no_stop_below_threshold(self):
        ev = CodingAgentEvaluator(max_consecutive_errors=3)
        trajectory = [
            StepResult(step_id=0, thought="t", status="success"),
            StepResult(step_id=1, thought="t", status="error"),
            StepResult(step_id=2, thought="t", status="error"),
        ]
        signal = ev.should_stop(trajectory)
        assert signal.should_stop is False

    def test_errors_not_at_end_dont_trigger(self):
        ev = CodingAgentEvaluator(max_consecutive_errors=3)
        trajectory = [
            StepResult(step_id=0, thought="t", status="error"),
            StepResult(step_id=1, thought="t", status="error"),
            StepResult(step_id=2, thought="t", status="success"),
        ]
        signal = ev.should_stop(trajectory)
        assert signal.should_stop is False

    def test_empty_trajectory(self):
        ev = CodingAgentEvaluator()
        signal = ev.should_stop([])
        assert signal.should_stop is False


# ── Spinning detection ──


class TestSpinningDetection:
    def test_stops_on_repeated_identical_actions(self):
        ev = CodingAgentEvaluator(max_action_repetitions=3)
        trajectory = [
            StepResult(step_id=i, thought="t", action={"name": "file_read", "arguments": {"path": "x.py"}})
            for i in range(3)
        ]
        signal = ev.should_stop(trajectory)
        assert signal.should_stop is True
        assert signal.stop_category == "spinning"

    def test_no_stop_different_args(self):
        ev = CodingAgentEvaluator(max_action_repetitions=3)
        trajectory = [
            StepResult(step_id=0, thought="t", action={"name": "file_read", "arguments": {"path": "a.py"}}),
            StepResult(step_id=1, thought="t", action={"name": "file_read", "arguments": {"path": "b.py"}}),
            StepResult(step_id=2, thought="t", action={"name": "file_read", "arguments": {"path": "c.py"}}),
        ]
        signal = ev.should_stop(trajectory)
        assert signal.should_stop is False

    def test_no_stop_no_actions(self):
        ev = CodingAgentEvaluator(max_action_repetitions=3)
        trajectory = [
            StepResult(step_id=0, thought="t"),
            StepResult(step_id=1, thought="t"),
        ]
        signal = ev.should_stop(trajectory)
        assert signal.should_stop is False

    def test_mixed_tools_not_spinning(self):
        ev = CodingAgentEvaluator(max_action_repetitions=3)
        trajectory = [
            StepResult(step_id=0, thought="t", action={"name": "file_read", "arguments": {"path": "x.py"}}),
            StepResult(step_id=1, thought="t", action={"name": "search_code", "arguments": {"pattern": "foo"}}),
            StepResult(step_id=2, thought="t", action={"name": "file_read", "arguments": {"path": "x.py"}}),
        ]
        signal = ev.should_stop(trajectory)
        assert signal.should_stop is False


# ── Stagnation detection ──


class TestStagnationDetection:
    def test_stops_on_identical_observations(self):
        ev = CodingAgentEvaluator(stagnation_window=4)
        trajectory = [
            StepResult(step_id=i, thought="t", observation="same result")
            for i in range(4)
        ]
        signal = ev.should_stop(trajectory)
        assert signal.should_stop is True
        assert signal.stop_category == "stagnation"

    def test_stops_on_empty_observations(self):
        ev = CodingAgentEvaluator(stagnation_window=3)
        trajectory = [
            StepResult(step_id=i, thought="t")
            for i in range(3)
        ]
        signal = ev.should_stop(trajectory)
        assert signal.should_stop is True
        assert signal.stop_category == "stagnation"

    def test_no_stop_progressing(self):
        ev = CodingAgentEvaluator(stagnation_window=4)
        trajectory = [
            StepResult(step_id=0, thought="t", observation="result a"),
            StepResult(step_id=1, thought="t", observation="result b"),
            StepResult(step_id=2, thought="t", observation="result c"),
            StepResult(step_id=3, thought="t", observation="result d"),
        ]
        signal = ev.should_stop(trajectory)
        assert signal.should_stop is False

    def test_below_window_no_stop(self):
        ev = CodingAgentEvaluator(stagnation_window=5)
        trajectory = [
            StepResult(step_id=i, thought="t", observation="same")
            for i in range(3)
        ]
        signal = ev.should_stop(trajectory)
        assert signal.should_stop is False


# ── Goal verification ──


class TestGoalVerification:
    def test_achieved(self):
        llm = FakeLLM("ACHIEVED: The bug was fixed.")
        ev = CodingAgentEvaluator(llm_client=llm)
        trajectory = [
            StepResult(step_id=0, thought="done", status="terminated"),
        ]
        result = ev.evaluate_success("Fix the bug", trajectory)
        assert result.achieved is True
        assert result.confidence == 0.8
        assert "ACHIEVED" in result.explanation

    def test_not_achieved(self):
        llm = FakeLLM("NOT_ACHIEVED: The file was not modified.")
        ev = CodingAgentEvaluator(llm_client=llm)
        trajectory = [
            StepResult(step_id=0, thought="tried", status="success", action={"name": "file_read", "arguments": {}}),
        ]
        result = ev.evaluate_success("Fix the bug", trajectory)
        assert result.achieved is False

    def test_fallback_without_llm(self):
        ev = CodingAgentEvaluator()
        trajectory = [
            StepResult(step_id=0, thought="done", status="terminated"),
        ]
        result = ev.evaluate_success("test", trajectory)
        assert result.achieved is True

    def test_fallback_no_query(self):
        llm = FakeLLM()
        ev = CodingAgentEvaluator(llm_client=llm)
        trajectory = [
            StepResult(step_id=0, thought="done", status="terminated"),
        ]
        # Empty query triggers fallback
        result = ev.evaluate_success("", trajectory)
        assert result.achieved is True

    def test_llm_error_returns_failure(self):
        class FailLLM:
            def chat(self, messages, tools=None):
                raise ConnectionError("API down")

        ev = CodingAgentEvaluator(llm_client=FailLLM())
        trajectory = [
            StepResult(step_id=0, thought="done", status="terminated"),
        ]
        result = ev.evaluate_success("test", trajectory)
        assert result.achieved is False
        assert "Verification failed" in result.explanation


# ── Report integration ──


class TestReportIntegration:
    def test_report_has_evaluation(self):
        ev = CodingAgentEvaluator()
        ev.log_step(StepResult(step_id=0, thought="done", status="terminated"))
        report = ev.get_report()
        assert "evaluation" in report["summary"]
        assert report["summary"]["success"] is True
        assert report["summary"]["evaluation"]["achieved"] is True

    def test_report_has_stop_reason(self):
        ev = CodingAgentEvaluator()
        ev.log_step(StepResult(
            step_id=0, thought="t",
            stop_signal={"should_stop": True, "reason": "spinning", "stop_category": "spinning"},
        ))
        report = ev.get_report()
        assert report["summary"]["stop_reason"] == "spinning"
