from typing import List, Dict
from nanoharness.core.base import BaseEvaluator
from nanoharness.core.schema import StepResult


class TraceEvaluator(BaseEvaluator):
    """Default evaluator: records trajectory, produces summary report.

    Provides baseline should_stop() and evaluate_success() via BaseEvaluator
    defaults (no mid-loop stopping, success = any terminated step).
    App-layer evaluators should subclass and override these methods.
    """

    def __init__(self):
        self.trajectory: List[StepResult] = []

    def log_step(self, step: StepResult):
        """Appends a completed step to the trajectory."""
        self.trajectory.append(step)

    def get_report(self, query: str = "") -> Dict:
        """Computes summary statistics from the recorded trajectory."""
        total_steps = len(self.trajectory)

        # Use evaluate_success for the official verdict
        evaluation = self.evaluate_success(query, self.trajectory)

        # Check if any step had a stop_signal (early stop)
        stop_reason = ""
        for s in self.trajectory:
            if s.stop_signal and s.stop_signal.should_stop:
                stop_reason = s.stop_signal.reason
                break

        return {
            "summary": {
                # evaluate_success is the official verdict. A model stopping
                # without tools only means the loop terminated, not that the
                # user's goal was necessarily achieved.
                "success": evaluation.achieved,
                "total_steps": total_steps,
                "avg_thought_length": sum(len(s.thought) for s in self.trajectory) / total_steps if total_steps > 0 else 0,
                "evaluation": evaluation.model_dump(),
                "stop_reason": stop_reason,
            },
            "trajectory": [s.model_dump() for s in self.trajectory],
        }

    def reset(self):
        """Clears the current trajectory for a new task."""
        self.trajectory = []
