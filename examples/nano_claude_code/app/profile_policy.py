from nanoharness.core.schema import PolicyDecision, PolicyOutcome, PolicyStage


class InteractiveProfilePolicy:
    """Deterministic counterpart of the terminal permission pipeline."""

    def decide(self, stage, request, execution=None):
        if stage == PolicyStage.AFTER_TOOL:
            return PolicyDecision(outcome=PolicyOutcome.ALLOW, source="nano_claude_code")
        if request.name == "workspace_write":
            return PolicyDecision(
                outcome=PolicyOutcome.REQUIRE_APPROVAL,
                reason="Interactive workspace writes require confirmation",
                source="nano_claude_code",
            )
        return PolicyDecision(outcome=PolicyOutcome.ALLOW, source="nano_claude_code")
