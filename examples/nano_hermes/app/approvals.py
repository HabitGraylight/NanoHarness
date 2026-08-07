"""Content-minimized approvals for NanoHermes actions and learning."""

from collections.abc import Callable
from typing import Any

from nanoharness.core.schema import (
    ApprovalResult,
    ApprovalStatus,
    PolicyDecision,
    ToolRequest,
)

from app.models import ActionApproval, HermesRunState, LearningProposal
from app.store import HermesRunStore


ActionDecider = bool | Callable[[ToolRequest, PolicyDecision], bool]
LearningDecider = bool | Callable[[LearningProposal], bool]


class TerminalActionDecider:
    def __init__(self, input_func=input, output_func=print):
        self._input = input_func
        self._output = output_func

    def __call__(self, request: ToolRequest, decision: PolicyDecision) -> bool:
        details = _safe_details(request.arguments)
        suffix = " ".join(f"{key}={value}" for key, value in details.items())
        self._output(decision.reason or "NanoHermes action approval required")
        prompt = f"Approve {request.name}"
        if suffix:
            prompt += f" ({suffix})"
        return self._input(prompt + "? [y/N] ").strip().lower() in {"y", "yes"}


class TerminalLearningDecider:
    def __init__(self, input_func=input, output_func=print):
        self._input = input_func
        self._output = output_func

    def __call__(self, proposal: LearningProposal) -> bool:
        self._output(
            f"Learning proposal {proposal.kind.value}:{proposal.name} "
            f"sha256={proposal.content_sha256}"
        )
        return self._input("Promote this proposal? [y/N] ").strip().lower() in {
            "y",
            "yes",
        }


class RecordingActionApprovalBroker:
    def __init__(
        self,
        state: HermesRunState,
        store: HermesRunStore,
        decider: ActionDecider,
    ):
        self._state = state
        self._store = store
        self._decider = decider

    def request_approval(
        self,
        request: ToolRequest,
        decision: PolicyDecision,
    ) -> ApprovalResult:
        approved = (
            bool(self._decider(request, decision))
            if callable(self._decider)
            else bool(self._decider)
        )
        reason = (
            "Approved by the NanoHermes host"
            if approved
            else "Denied by the NanoHermes host"
        )
        self._state.action_approvals.append(ActionApproval(
            call_id=request.call_id,
            tool=request.name,
            approved=approved,
            reason=reason,
            details=_safe_details(request.arguments),
        ))
        self._store.save(self._state)
        return ApprovalResult(
            status=(
                ApprovalStatus.APPROVED
                if approved
                else ApprovalStatus.DENIED
            ),
            reason=reason,
            metadata={"recorded": True},
        )


def _safe_details(arguments: dict[str, Any]) -> dict[str, str]:
    safe = {}
    for key in ("path", "id", "name", "cron", "delay_seconds"):
        value = arguments.get(key)
        if value is not None:
            safe[key] = str(value)
    return safe
