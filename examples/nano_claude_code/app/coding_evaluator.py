"""App-layer evaluator for the coding agent.

Implements the V-component's three evaluation dimensions:
1. Trajectories  -- inherited from TraceEvaluator
2. Mid-loop      -- detect spinning, error loops, stagnation
3. Post-loop     -- LLM-based goal verification (replaces standalone verify_goal)
"""

from collections import Counter
from typing import List, Optional

from nanoharness.components.evaluator.trace_evaluator import TraceEvaluator
from nanoharness.core.schema import EvaluationResult, StepResult, StopSignal


class CodingAgentEvaluator(TraceEvaluator):
    """Production evaluator for the coding agent.

    Subclass TraceEvaluator to inherit trajectory recording and report
    generation, then override should_stop and evaluate_success.
    """

    def __init__(
        self,
        llm_client=None,
        *,
        max_consecutive_errors: int = 3,
        max_action_repetitions: int = 4,
        stagnation_window: int = 5,
    ):
        super().__init__()
        self._llm = llm_client
        self._max_consecutive_errors = max_consecutive_errors
        self._max_action_repetitions = max_action_repetitions
        self._stagnation_window = stagnation_window

    # ── Mid-loop: should_stop ──

    def should_stop(self, trajectory: List[StepResult]) -> StopSignal:
        """Detect spinning, error loops, and stagnation."""
        if not trajectory:
            return StopSignal()

        # Check 1: Consecutive error loop
        signal = self._check_error_loop(trajectory)
        if signal.should_stop:
            return signal

        # Check 2: Repeated actions (spinning)
        signal = self._check_spinning(trajectory)
        if signal.should_stop:
            return signal

        # Check 3: Stagnation (no meaningful progress)
        signal = self._check_stagnation(trajectory)
        if signal.should_stop:
            return signal

        return StopSignal()

    def _check_error_loop(self, trajectory: List[StepResult]) -> StopSignal:
        """Detect N consecutive steps with status='error'."""
        consecutive = 0
        for step in reversed(trajectory):
            if step.status == "error":
                consecutive += 1
            else:
                break

        if consecutive >= self._max_consecutive_errors:
            return StopSignal(
                should_stop=True,
                reason=f"{consecutive} consecutive error steps (threshold: {self._max_consecutive_errors})",
                stop_category="error_loop",
            )
        return StopSignal()

    def _check_spinning(self, trajectory: List[StepResult]) -> StopSignal:
        """Detect repeated tool calls with identical name + arguments."""
        action_counts: Counter = Counter()
        for step in trajectory:
            action = step.action
            if not action:
                continue
            key = (action.get("name", ""), str(action.get("arguments", {})))
            action_counts[key] += 1

        for (name, _args_str), count in action_counts.items():
            if count >= self._max_action_repetitions and name:
                return StopSignal(
                    should_stop=True,
                    reason=f"Tool '{name}' called {count} times with same args (threshold: {self._max_action_repetitions})",
                    stop_category="spinning",
                )
        return StopSignal()

    def _check_stagnation(self, trajectory: List[StepResult]) -> StopSignal:
        """Detect stagnation: last K steps have no meaningful observation change."""
        if len(trajectory) < self._stagnation_window:
            return StopSignal()

        recent = trajectory[-self._stagnation_window:]
        observations = [s.observation or "" for s in recent]

        # All empty
        if all(not obs for obs in observations):
            return StopSignal(
                should_stop=True,
                reason=f"No observations in last {self._stagnation_window} steps",
                stop_category="stagnation",
            )

        # All identical
        if len(set(observations)) == 1:
            return StopSignal(
                should_stop=True,
                reason=f"Identical observations in last {self._stagnation_window} steps",
                stop_category="stagnation",
            )

        return StopSignal()

    # ── Post-loop: evaluate_success ──

    def evaluate_success(self, query: str, trajectory: List[StepResult]) -> EvaluationResult:
        """LLM-based goal verification. Falls back to legacy logic if no LLM."""
        if not self._llm or not query:
            return super().evaluate_success(query, trajectory)

        return _llm_verify_goal(self._llm, query, trajectory)


def _llm_verify_goal(llm_client, query: str, trajectory: List[StepResult]) -> EvaluationResult:
    """Use LLM to independently verify if the agent achieved its goal."""
    steps_summary = []
    for i, step in enumerate(trajectory):
        action = step.action or {}
        tool_name = action.get("name", "none")
        obs = (step.observation or "")[:200]
        steps_summary.append(f"  Step {i} [{step.status}]: tool={tool_name}, obs={obs}")

    trajectory_text = "\n".join(steps_summary)
    final_thought = trajectory[-1].thought[:500] if trajectory else ""

    prompt = f"""You are evaluating whether an AI agent achieved its goal.

Original task: {query}

Agent trajectory:
{trajectory_text}

Final answer from agent: {final_thought}

Did the agent achieve the original task goal?
Reply with EXACTLY one of:
- ACHIEVED: <one sentence explanation>
- NOT_ACHIEVED: <one sentence explanation>"""

    try:
        response = llm_client.chat(
            messages=[{"role": "user", "content": prompt}],
            tools=None,
        )
        content = (response.content or "").strip()
        achieved = content.upper().startswith("ACHIEVED")
        return EvaluationResult(
            achieved=achieved,
            confidence=0.8,
            explanation=content,
            evidence=[s.observation[:100] for s in trajectory[-3:] if s.observation],
        )
    except Exception as exc:
        return EvaluationResult(
            achieved=False,
            confidence=0.0,
            explanation=f"Verification failed: {exc}",
            evidence=[],
        )
