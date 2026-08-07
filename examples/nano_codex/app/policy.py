from nanoharness.core.schema import PolicyDecision, PolicyOutcome, PolicyStage


class CodexPolicy:
    """Controlled coding policy with inspectable phase tool allowlists."""

    PHASE_TOOLS = {
        "plan": {
            "plan_submit",
            "skill",
            "task_list",
            "workspace_diff",
            "workspace_list",
            "workspace_read",
            "workspace_search",
            "workspace_status",
        },
        "execute": {
            "execution_finish",
            "task",
            "task_list",
            "workspace_diff",
            "workspace_list",
            "workspace_patch",
            "workspace_read",
            "workspace_search",
            "workspace_status",
            "workspace_test",
            "workspace_write",
        },
        "review": {
            "delivery_submit",
            "review_submit",
            "task_list",
            "workspace_diff",
            "workspace_list",
            "workspace_read",
            "workspace_search",
            "workspace_status",
            "workspace_test",
            "worktree_list",
        },
    }

    def __init__(self, state=None):
        self._state = state

    def decide(self, stage, request, execution=None):
        if stage == PolicyStage.AFTER_TOOL:
            return PolicyDecision(outcome=PolicyOutcome.ALLOW, source="nano_codex")
        if request.name == "channel_send":
            return PolicyDecision(
                outcome=PolicyOutcome.DENY,
                reason="NanoCodex runs cannot send external channel messages",
                source="nano_codex",
            )
        if self._state is not None:
            phase = getattr(self._state.phase, "value", self._state.phase)
            allowed = self.PHASE_TOOLS.get(str(phase), set())
            if request.name not in allowed:
                return PolicyDecision(
                    outcome=PolicyOutcome.DENY,
                    reason=f"Tool {request.name!r} is unavailable during {phase} phase",
                    source="nano_codex",
                    metadata={"execution_status": "blocked", "phase": phase},
                )
        if request.name in {"delivery_submit", "workspace_patch", "workspace_write"}:
            return PolicyDecision(
                outcome=PolicyOutcome.REQUIRE_APPROVAL,
                reason=f"{request.name} requires host approval",
                source="nano_codex",
            )
        return PolicyDecision(outcome=PolicyOutcome.ALLOW, source="nano_codex")
