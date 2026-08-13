"""Turn-aware tool policy for the NanoOpenClaw application layer."""

from __future__ import annotations

from typing import TYPE_CHECKING

from nanoharness.core.schema import PolicyDecision, PolicyOutcome, PolicyStage

if TYPE_CHECKING:
    from app.models import GatewayTurnState


class GatewayPolicy:
    """Allow read-only inspection and an explicit response transition only."""

    def __init__(self, state: GatewayTurnState | None = None):
        self.state = state

    def decide(self, stage, request, execution=None):
        if stage == PolicyStage.AFTER_TOOL:
            return PolicyDecision(
                outcome=PolicyOutcome.ALLOW,
                source="nano_openclaw",
            )
        if request.name == "workspace_read":
            return PolicyDecision(
                outcome=PolicyOutcome.ALLOW,
                source="nano_openclaw",
            )
        if request.name == "response_submit":
            phase = getattr(self.state, "phase", "respond")
            phase_value = getattr(phase, "value", phase)
            if self.state is not None and phase_value != "respond":
                return PolicyDecision(
                    outcome=PolicyOutcome.DENY,
                    reason="The response transition has already been submitted",
                    source="nano_openclaw",
                )
            return PolicyDecision(
                outcome=PolicyOutcome.ALLOW,
                source="nano_openclaw",
            )
        return PolicyDecision(
            outcome=PolicyOutcome.DENY,
            reason=(
                f"NanoOpenClaw does not expose tool {request.name!r}; channel "
                "delivery and workspace mutation remain host-controlled"
            ),
            source="nano_openclaw",
        )
