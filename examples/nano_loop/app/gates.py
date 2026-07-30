"""Human approval gates for irreversible follow-up actions."""

from typing import List

from app.schema import LoopSpec


class HumanGate:
    """Describe actions that require approval before a run is complete.

    Version one records the gate and waits. It intentionally does not execute
    push, merge, deployment, deletion, or issue mutations on approval.
    """

    def required_actions(self, spec: LoopSpec) -> List[str]:
        return list(dict.fromkeys(spec.gates.require_human))
