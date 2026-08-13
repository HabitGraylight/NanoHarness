"""Outbound review decisions kept separate from the model tool loop."""

from __future__ import annotations

from collections.abc import Callable

from nanoharness.extensions.channels import OutboundEnvelope


OutboundDecider = bool | Callable[[OutboundEnvelope], bool]


def decide_outbound(
    decider: OutboundDecider,
    envelope: OutboundEnvelope,
) -> tuple[bool, str]:
    approved = decider if isinstance(decider, bool) else bool(decider(envelope))
    return (
        bool(approved),
        "approved by NanoOpenClaw host"
        if approved
        else "rejected by NanoOpenClaw host",
    )


class TerminalOutboundDecider:
    """Interactive decider that displays route metadata, never the audit object."""

    def __init__(self, reader=input, writer=print):
        self.reader = reader
        self.writer = writer

    def __call__(self, envelope: OutboundEnvelope) -> bool:
        self.writer(
            "Outbound response: "
            f"channel={envelope.channel} account={envelope.account_id} "
            f"conversation={envelope.conversation_id} "
            f"recipient={envelope.recipient_id or '-'} "
            f"length={len(envelope.content)}"
        )
        answer = self.reader("Approve delivery? [y/N] ").strip().lower()
        return answer in {"y", "yes"}
