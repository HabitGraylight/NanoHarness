"""Inspectable approval boundary for NanoCodex workspace mutations."""

from collections.abc import Callable
from typing import Any

from nanoharness.core.schema import (
    ApprovalResult,
    ApprovalStatus,
    PolicyDecision,
    ToolRequest,
)

from app.models import ApprovalRecord, CodexRunState
from app.store import CodexRunStore


ApprovalDecider = bool | Callable[[ToolRequest, PolicyDecision], bool]


class TerminalApprovalDecider:
    """Interactive approval callback that never displays file contents."""

    def __init__(self, input_func=input, output_func=print):
        self._input = input_func
        self._output = output_func

    def __call__(self, request: ToolRequest, decision: PolicyDecision) -> bool:
        details = _safe_details(request.arguments)
        suffix = " ".join(f"{key}={value}" for key, value in details.items())
        prompt = f"Approve {request.name}"
        if suffix:
            prompt += f" ({suffix})"
        self._output(decision.reason or "NanoCodex approval required")
        answer = self._input(prompt + "? [y/N] ").strip().lower()
        return answer in {"y", "yes"}


class RecordingApprovalBroker:
    """Resolve and persist approvals without recording file contents."""

    def __init__(
        self,
        state: CodexRunState,
        store: CodexRunStore,
        decider: ApprovalDecider = True,
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
            "Approved by the NanoCodex host"
            if approved
            else "Denied by the NanoCodex host"
        )
        details = _safe_details(request.arguments)
        path = details.get("path")
        self._state.approvals.append(ApprovalRecord(
            call_id=request.call_id,
            tool=request.name,
            approved=approved,
            reason=reason,
            path=path,
            details=details,
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
    """Keep identifiers useful for audit while excluding content and raw commands."""

    safe = {}
    for key in ("path", "mode", "name"):
        value = arguments.get(key)
        if value is not None:
            safe[key] = str(value)
    return safe
