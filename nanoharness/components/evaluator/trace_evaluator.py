from typing import List, Dict
from nanoharness.core.base import BaseEvaluator
from nanoharness.core.schema import StepResult

class TraceEvaluator(BaseEvaluator):
    """
    Records execution trajectories and generates performance reports.
    Essential for analyzing agent success rates and intermediate reasoning.
    """
    def __init__(self):
        self.trajectory: List[StepResult] = []

    def log_step(self, step: StepResult):
        """Appends a completed step to the trajectory."""
        self.trajectory.append(step)

    def get_report(self) -> Dict:
        """Computes summary statistics from the recorded trajectory."""
        success = any(s.status == "terminated" for s in self.trajectory)
        total_steps = len(self.trajectory)
        
        return {
            "summary": {
                "success": success,
                "total_steps": total_steps,
                "avg_thought_length": sum(len(s.thought) for s in self.trajectory) / total_steps if total_steps > 0 else 0
            },
            "trajectory": [s.model_dump() for s in self.trajectory]
        }

    def reset(self):
        """Clears the current trajectory for a new task."""
        self.trajectory = []