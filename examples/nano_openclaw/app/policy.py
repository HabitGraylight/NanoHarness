from nanoharness.core.schema import PolicyDecision, PolicyOutcome, PolicyStage


class GatewayPolicy:
    """Gateway runs may deliver messages but cannot mutate the workspace."""

    def decide(self, stage, request, execution=None):
        if stage == PolicyStage.AFTER_TOOL:
            return PolicyDecision(outcome=PolicyOutcome.ALLOW, source="nano_openclaw")
        if request.name == "workspace_write":
            return PolicyDecision(
                outcome=PolicyOutcome.DENY,
                reason="Gateway sessions cannot mutate the workspace",
                source="nano_openclaw",
            )
        return PolicyDecision(outcome=PolicyOutcome.ALLOW, source="nano_openclaw")


def contribute_gateway_tools(tools, workspace):
    @tools.tool
    def channel_send(channel: str, content: str) -> str:
        """Deliver through a deterministic mock channel adapter."""
        return f"delivered to {channel}: {len(content)} chars"
