from nanoharness.core.schema import PolicyDecision, PolicyOutcome, PolicyStage


class CodexPolicy:
    """Controlled coding policy: writes need approval; channels are unavailable."""

    def decide(self, stage, request, execution=None):
        if stage == PolicyStage.AFTER_TOOL:
            return PolicyDecision(outcome=PolicyOutcome.ALLOW, source="nano_codex")
        if request.name == "channel_send":
            return PolicyDecision(
                outcome=PolicyOutcome.DENY,
                reason="NanoCodex runs cannot send external channel messages",
                source="nano_codex",
            )
        if request.name == "workspace_write":
            return PolicyDecision(
                outcome=PolicyOutcome.REQUIRE_APPROVAL,
                reason="Workspace writes require host approval",
                source="nano_codex",
            )
        return PolicyDecision(outcome=PolicyOutcome.ALLOW, source="nano_codex")
