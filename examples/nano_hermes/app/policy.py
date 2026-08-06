import re

from nanoharness.core.schema import PolicyDecision, PolicyOutcome, PolicyStage


class HermesPolicy:
    """Stage learning and delivery changes for explicit review."""

    def decide(self, stage, request, execution=None):
        if stage == PolicyStage.AFTER_TOOL:
            return PolicyDecision(outcome=PolicyOutcome.ALLOW, source="nano_hermes")
        if request.name in {"workspace_write", "skill_propose", "channel_send"}:
            return PolicyDecision(
                outcome=PolicyOutcome.REQUIRE_APPROVAL,
                reason="Persistent learning and external effects require review",
                source="nano_hermes",
            )
        return PolicyDecision(outcome=PolicyOutcome.ALLOW, source="nano_hermes")


def contribute_hermes_tools(tools, workspace):
    @tools.tool
    def skill_propose(name: str, content: str) -> str:
        """Stage a learned skill without modifying the active skill catalog."""
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", name):
            raise ValueError("Skill name contains unsupported characters")
        target = (workspace / ".nano_hermes" / "pending_skills" / f"{name}.md").resolve()
        if workspace not in target.parents:
            raise ValueError("Skill proposal escapes workspace")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return f"staged skill proposal {name}"
