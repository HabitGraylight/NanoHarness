"""Approval broker implementations kept separate from policy evaluation."""

from typing import Callable, Union

from nanoharness.core.schema import (
    ApprovalResult,
    ApprovalStatus,
    PolicyDecision,
    ToolRequest,
)


ApprovalCallback = Callable[
    [ToolRequest, PolicyDecision],
    Union[bool, ApprovalResult],
]


class CallbackApprovalBroker:
    """Resolve approvals through a UI, API, or test callback."""

    def __init__(self, callback: ApprovalCallback):
        self._callback = callback

    def request_approval(
        self,
        request: ToolRequest,
        decision: PolicyDecision,
    ) -> ApprovalResult:
        result = self._callback(request, decision)
        if isinstance(result, ApprovalResult):
            return result
        return ApprovalResult(
            status=(
                ApprovalStatus.APPROVED
                if result
                else ApprovalStatus.DENIED
            ),
            reason="Approved by callback" if result else "Denied by callback",
        )
