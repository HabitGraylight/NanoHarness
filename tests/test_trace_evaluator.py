from nanoharness.components.evaluator.trace_evaluator import TraceEvaluator
from nanoharness.core.schema import StepResult


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
