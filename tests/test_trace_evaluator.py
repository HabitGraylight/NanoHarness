from nanoharness.components.evaluator.trace_evaluator import TraceEvaluator
from nanoharness.core.schema import StepResult, StopSignal, EvaluationResult


class TestTraceEvaluator:
    def test_empty_report(self):
        ev = TraceEvaluator()
        report = ev.get_report()
        assert report["summary"]["success"] is False
        assert report["summary"]["total_steps"] == 0

    def test_log_and_report_success(self):
        ev = TraceEvaluator()
        ev.log_step(StepResult(step_id=0, thought="thinking", status="terminated"))
        report = ev.get_report()
        assert report["summary"]["success"] is True
        assert report["summary"]["total_steps"] == 1
        assert len(report["trajectory"]) == 1

    def test_log_and_report_failure(self):
        ev = TraceEvaluator()
        ev.log_step(StepResult(step_id=0, thought="trying", status="error"))
        ev.log_step(StepResult(step_id=1, thought="trying again", status="error"))
        report = ev.get_report()
        assert report["summary"]["success"] is False
        assert report["summary"]["total_steps"] == 2

    def test_avg_thought_length(self):
        ev = TraceEvaluator()
        ev.log_step(StepResult(step_id=0, thought="abc"))
        ev.log_step(StepResult(step_id=1, thought="abcdef"))
        report = ev.get_report()
        assert report["summary"]["avg_thought_length"] == 4.5

    def test_reset(self):
        ev = TraceEvaluator()
        ev.log_step(StepResult(step_id=0, thought="x", status="terminated"))
        ev.reset()
        assert ev.get_report()["summary"]["total_steps"] == 0

    def test_default_should_stop(self):
        ev = TraceEvaluator()
        ev.log_step(StepResult(step_id=0, thought="t", status="success"))
        ev.log_step(StepResult(step_id=1, thought="t", status="error"))
        signal = ev.should_stop(ev.trajectory)
        assert signal.should_stop is False

    def test_default_evaluate_success(self):
        ev = TraceEvaluator()
        ev.log_step(StepResult(step_id=0, thought="t", status="terminated"))
        result = ev.evaluate_success("test query", ev.trajectory)
        assert result.achieved is True
        assert result.confidence == 1.0

    def test_report_contains_evaluation(self):
        ev = TraceEvaluator()
        ev.log_step(StepResult(step_id=0, thought="done", status="terminated"))
        report = ev.get_report()
        assert "evaluation" in report["summary"]
        assert report["summary"]["evaluation"]["achieved"] is True
        assert "stop_reason" in report["summary"]
        assert report["summary"]["stop_reason"] == ""
