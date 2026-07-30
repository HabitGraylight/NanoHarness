"""Budgeted retry and terminal-state decisions."""

from dataclasses import dataclass

from app.gates import HumanGate
from app.schema import LoopState, LoopStatus, VerificationResult


@dataclass(frozen=True)
class PolicyDecision:
    status: LoopStatus
    reason: str


class LoopPolicy:
    def __init__(self, gate: HumanGate | None = None):
        self.gate = gate or HumanGate()

    def decide(
        self,
        state: LoopState,
        verification: VerificationResult,
        now: float,
    ) -> PolicyDecision:
        if verification.passed:
            required = self.gate.required_actions(state.spec)
            state.pending_gates = required
            if required:
                return PolicyDecision(
                    LoopStatus.WAITING_HUMAN,
                    "Verification passed; human approval is required",
                )
            return PolicyDecision(
                LoopStatus.COMPLETED,
                "All configured verification evidence passed",
            )

        elapsed = now - state.started_at
        if elapsed >= state.spec.budget.max_wall_seconds:
            return PolicyDecision(
                LoopStatus.BUDGET_EXHAUSTED,
                f"Wall-clock budget exhausted after {elapsed:.1f}s",
            )
        if state.iteration_count >= state.spec.budget.max_iterations:
            return PolicyDecision(
                LoopStatus.BUDGET_EXHAUSTED,
                f"Iteration budget exhausted at {state.iteration_count}",
            )
        if state.consecutive_failures >= state.spec.budget.max_consecutive_failures:
            return PolicyDecision(
                LoopStatus.BLOCKED,
                (
                    "Consecutive failure threshold reached at "
                    f"{state.consecutive_failures}"
                ),
            )
        return PolicyDecision(
            LoopStatus.RETRYING,
            "Verification failed with retryable evidence",
        )
