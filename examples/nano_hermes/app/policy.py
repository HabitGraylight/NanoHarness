from nanoharness.core.schema import PolicyDecision, PolicyOutcome, PolicyStage


class HermesPolicy:
    """Phase-aware personal-agent policy with staged durable learning."""

    PHASE_TOOLS = {
        "assist": {
            "assist_submit",
            "list_memories",
            "recall_memory",
            "schedule_create",
            "schedule_delete",
            "schedule_list",
            "schedule_pause",
            "schedule_resume",
            "skill",
            "task",
            "workspace_read",
            "workspace_write",
        },
        "reflect": {
            "list_memories",
            "memory_propose",
            "recall_memory",
            "reflection_submit",
            "skill",
            "skill_propose",
            "workspace_read",
        },
    }

    def __init__(self, state=None):
        self._state = state

    def decide(self, stage, request, execution=None):
        if stage == PolicyStage.AFTER_TOOL:
            return PolicyDecision(outcome=PolicyOutcome.ALLOW, source="nano_hermes")
        if request.name in {"channel_send", "save_memory"}:
            return PolicyDecision(
                outcome=PolicyOutcome.DENY,
                reason=(
                    "NanoHermes cannot deliver externally or write durable memory "
                    "without a staged proposal"
                ),
                source="nano_hermes",
            )
        if self._state is not None:
            phase = getattr(self._state.phase, "value", self._state.phase)
            allowed = self.PHASE_TOOLS.get(str(phase), set())
            if request.name not in allowed:
                return PolicyDecision(
                    outcome=PolicyOutcome.DENY,
                    reason=f"Tool {request.name!r} is unavailable during {phase} phase",
                    source="nano_hermes",
                    metadata={"execution_status": "blocked", "phase": phase},
                )
        if request.name in {
            "schedule_create",
            "schedule_delete",
            "schedule_pause",
            "schedule_resume",
            "workspace_write",
        }:
            return PolicyDecision(
                outcome=PolicyOutcome.REQUIRE_APPROVAL,
                reason="Persistent workspace and schedule changes require host approval",
                source="nano_hermes",
            )
        return PolicyDecision(outcome=PolicyOutcome.ALLOW, source="nano_hermes")
