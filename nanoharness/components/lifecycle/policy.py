"""Composable implementations of the typed tool policy protocol."""

from typing import Iterable, Optional

from nanoharness.core.base import ToolPolicyProtocol
from nanoharness.core.schema import (
    PolicyDecision,
    PolicyOutcome,
    PolicyStage,
    ToolExecution,
    ToolRequest,
)


class AllowAllPolicy:
    """Explicit policy for harness profiles that allow every tool call."""

    def decide(
        self,
        stage: PolicyStage,
        request: ToolRequest,
        execution: Optional[ToolExecution] = None,
    ) -> PolicyDecision:
        return PolicyDecision(outcome=PolicyOutcome.ALLOW, source="allow_all")


class CompositeToolPolicy:
    """Combine policies with DENY > REQUIRE_APPROVAL > ALLOW precedence."""

    def __init__(self, policies: Iterable[ToolPolicyProtocol]):
        self._policies = list(policies)

    def decide(
        self,
        stage: PolicyStage,
        request: ToolRequest,
        execution: Optional[ToolExecution] = None,
    ) -> PolicyDecision:
        outcome = PolicyOutcome.ALLOW
        reasons = []
        sources = []
        context = []
        suffixes = []
        decisions = []
        execution_status = None

        for policy in self._policies:
            decision = policy.decide(stage, request, execution)
            decisions.append(decision.model_dump(mode="json"))
            if decision.reason:
                reasons.append(decision.reason)
            if decision.source:
                sources.append(decision.source)
            if decision.context_injection:
                context.append(decision.context_injection)
            if decision.output_suffix:
                suffixes.append(decision.output_suffix)
            if decision.metadata.get("execution_status"):
                execution_status = decision.metadata["execution_status"]

            if decision.outcome == PolicyOutcome.DENY:
                outcome = PolicyOutcome.DENY
                break
            if decision.outcome == PolicyOutcome.REQUIRE_APPROVAL:
                outcome = PolicyOutcome.REQUIRE_APPROVAL

        return PolicyDecision(
            outcome=outcome,
            reason="; ".join(reasons),
            source="+".join(sources) or "composite",
            context_injection="\n".join(context) or None,
            output_suffix="\n".join(suffixes) or None,
            metadata={
                "decisions": decisions,
                **(
                    {"execution_status": execution_status}
                    if execution_status
                    else {}
                ),
            },
        )
