"""Crash-aware, evidence-gated orchestration across NanoEngine runs."""

import re
import time
import uuid
from typing import Callable, Optional

from app.policy import LoopPolicy, PolicyDecision
from app.schema import (
    Evidence,
    IterationRecord,
    LoopSpec,
    LoopState,
    LoopStatus,
    VerificationResult,
)
from app.store import JsonLoopStore
from app.verifier import Verifier
from app.worker import LoopWorker, WorkerConfigurationError
from app.workspace import WorkspaceProvider


class LoopRunner:
    """Run a bounded outer loop until evidence passes or policy stops it."""

    def __init__(
        self,
        store: JsonLoopStore,
        workspace_provider: WorkspaceProvider,
        worker: LoopWorker,
        verifier: Verifier,
        policy: Optional[LoopPolicy] = None,
        clock: Callable[[], float] = time.time,
        id_factory: Optional[Callable[[LoopSpec], str]] = None,
    ):
        self.store = store
        self.workspace_provider = workspace_provider
        self.worker = worker
        self.verifier = verifier
        self.policy = policy or LoopPolicy()
        self.clock = clock
        self.id_factory = id_factory or _new_run_id

    def start(self, spec: LoopSpec, task: str, repository: str) -> LoopState:
        if not task.strip():
            raise ValueError("Loop task cannot be empty")
        now = self.clock()
        state = LoopState(
            run_id=self.id_factory(spec),
            spec=spec,
            task=task.strip(),
            repository=repository,
            started_at=now,
            updated_at=now,
        )
        if self.store.exists(state.run_id):
            raise ValueError(f"Loop run already exists: {state.run_id}")
        self._save(state)
        return self._prepare_and_drive(state)

    def resume(self, run_id: str) -> LoopState:
        state = self.store.load(run_id)
        if state.status == LoopStatus.WAITING_HUMAN or state.is_terminal:
            return state

        if state.iterations and state.iterations[-1].status in {
            "running",
            "verifying",
        }:
            interrupted = state.iterations[-1]
            interrupted.status = "interrupted"
            interrupted.completed_at = self.clock()
            interrupted.error = "Previous process stopped before the iteration completed"
            state.consecutive_failures += 1
            state.feedback = interrupted.error
            self._save(state)

        return self._prepare_and_drive(state)

    def approve(self, run_id: str) -> LoopState:
        state = self.store.load(run_id)
        if state.status != LoopStatus.WAITING_HUMAN:
            raise ValueError(
                f"Run '{run_id}' is {state.status.value}, not waiting for approval"
            )
        state.pending_gates = []
        state.status = LoopStatus.COMPLETED
        state.stop_reason = "Verification evidence approved by a human"
        state.completed_at = self.clock()
        self._save(state)
        return state

    def reject(self, run_id: str, reason: str) -> LoopState:
        state = self.store.load(run_id)
        if state.status != LoopStatus.WAITING_HUMAN:
            raise ValueError(
                f"Run '{run_id}' is {state.status.value}, not waiting for approval"
            )
        state.status = LoopStatus.BLOCKED
        state.stop_reason = reason.strip() or "Human approval rejected"
        state.completed_at = self.clock()
        self._save(state)
        return state

    def _prepare_and_drive(self, state: LoopState) -> LoopState:
        if state.workspace is None:
            state.status = LoopStatus.PREPARING
            self._save(state)
            try:
                state.workspace = self.workspace_provider.create(
                    state.run_id,
                    state.repository,
                    state.spec.workspace.base_ref,
                )
            except Exception as exc:
                state.status = LoopStatus.FAILED
                state.stop_reason = f"Workspace preparation failed: {exc}"
                state.completed_at = self.clock()
                self._save(state)
                return state
            self._save(state)
        return self._drive(state)

    def _drive(self, state: LoopState) -> LoopState:
        while not state.is_terminal and state.status != LoopStatus.WAITING_HUMAN:
            preflight = self._preflight_decision(state)
            if preflight:
                self._apply_decision(state, preflight)
                break

            iteration_number = state.iteration_count + 1
            record = IterationRecord(
                number=iteration_number,
                status="running",
                started_at=self.clock(),
            )
            state.iterations.append(record)
            state.status = LoopStatus.RUNNING
            self._save(state)

            try:
                record.worker = self.worker.run(
                    goal=_worker_contract(state),
                    task=state.task,
                    feedback=state.feedback,
                    workspace=state.workspace.path,
                    iteration=iteration_number,
                    run_id=state.run_id,
                )
            except WorkerConfigurationError as exc:
                verification = VerificationResult(
                    passed=False,
                    evidence=[
                        Evidence(
                            kind="worker_configuration",
                            passed=False,
                            summary=f"Worker configuration failed: {exc}",
                        )
                    ],
                    feedback=f"Worker configuration failed: {exc}",
                )
                record.verification = verification
                record.completed_at = self.clock()
                record.status = "failed"
                record.error = str(exc)
                state.feedback = verification.feedback
                state.consecutive_failures += 1
                state.status = LoopStatus.FAILED
                state.stop_reason = verification.feedback
                state.completed_at = self.clock()
                self._save(state)
                continue
            except Exception as exc:
                verification = VerificationResult(
                    passed=False,
                    evidence=[
                        Evidence(
                            kind="worker_error",
                            passed=False,
                            summary=f"Worker failed: {exc}",
                        )
                    ],
                    feedback=f"Worker failed before verification: {exc}",
                )
                self._finish_iteration(state, record, verification, error=str(exc))
                continue

            record.status = "verifying"
            state.status = LoopStatus.VERIFYING
            self._save(state)

            try:
                verification = self.verifier.verify(state.workspace.path)
                self._finish_iteration(state, record, verification)
            except Exception as exc:
                verification = VerificationResult(
                    passed=False,
                    evidence=[
                        Evidence(
                            kind="verifier_error",
                            passed=False,
                            summary=f"Verifier failed: {exc}",
                        )
                    ],
                    feedback=f"Verifier failed unexpectedly: {exc}",
                )
                self._finish_iteration(state, record, verification, error=str(exc))

        return state

    def _finish_iteration(
        self,
        state: LoopState,
        record: IterationRecord,
        verification: VerificationResult,
        error: str = "",
    ) -> None:
        record.verification = verification
        record.completed_at = self.clock()
        record.status = "passed" if verification.passed else "failed"
        record.error = error
        state.feedback = verification.feedback
        if verification.passed:
            state.consecutive_failures = 0
        else:
            state.consecutive_failures += 1

        decision = self.policy.decide(state, verification, self.clock())
        self._apply_decision(state, decision)

    def _preflight_decision(self, state: LoopState) -> Optional[PolicyDecision]:
        now = self.clock()
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
        return None

    def _apply_decision(self, state: LoopState, decision: PolicyDecision) -> None:
        state.status = decision.status
        state.stop_reason = decision.reason
        if state.is_terminal:
            state.completed_at = self.clock()
        self._save(state)

    def _save(self, state: LoopState) -> None:
        state.updated_at = self.clock()
        self.store.save(state)


def _new_run_id(spec: LoopSpec) -> str:
    prefix = re.sub(r"[^a-zA-Z0-9-]+", "-", spec.name).strip("-").lower()
    prefix = prefix[:32] or "loop"
    timestamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    return f"{prefix}-{timestamp}-{uuid.uuid4().hex[:8]}"


def _worker_contract(state: LoopState) -> str:
    contract = state.spec.goal.strip()
    if state.spec.verify.commands:
        commands = "\n".join(f"- {command}" for command in state.spec.verify.commands)
        contract += "\n\nIndependent acceptance commands:\n" + commands
    return contract
