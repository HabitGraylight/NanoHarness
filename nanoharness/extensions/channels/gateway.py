"""Host-facing orchestration for durable channel ingress and delivery."""

from __future__ import annotations

import re
from typing import Any, Iterable, Mapping, Optional

from nanoharness.extensions.channels.adapter import (
    ChannelAdapterProtocol,
    ChannelDeliveryError,
)
from nanoharness.extensions.channels.models import (
    InboxRecord,
    InboundEnvelope,
    OutboundEnvelope,
    OutboxRecord,
    OutboxStatus,
    stable_tool_idempotency_key,
)
from nanoharness.extensions.channels.store import DurableChannelStore


class DurableChannelGateway:
    """Durable data plane; application hosts own routing and policy."""

    def __init__(
        self,
        store: DurableChannelStore,
        adapters: Optional[Iterable[ChannelAdapterProtocol]] = None,
        *,
        recover: bool = True,
    ):
        self.store = store
        self._adapters: dict[str, ChannelAdapterProtocol] = {}
        self._closed = False
        for adapter in adapters or []:
            self.register_adapter(adapter)
        if recover:
            self.store.recover_expired_claims()
            self.store.recover_sending()

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def channels(self) -> list[str]:
        return sorted(self._adapters)

    def register_adapter(
        self,
        adapter: ChannelAdapterProtocol,
        *,
        replace: bool = False,
    ) -> None:
        self._require_open()
        channel = adapter.channel
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", channel):
            raise ValueError("channel adapter name is invalid")
        if channel in self._adapters and not replace:
            raise ValueError(f"channel adapter already registered: {channel}")
        if replace:
            previous = self._adapters.get(channel)
            if previous is not None:
                previous.close()
        self._adapters[channel] = adapter

    def ingest(
        self,
        envelope: InboundEnvelope | Mapping[str, Any],
    ) -> tuple[InboxRecord, bool]:
        self._require_open()
        parsed = (
            envelope
            if isinstance(envelope, InboundEnvelope)
            else InboundEnvelope.model_validate(envelope)
        )
        return self.store.ingest(parsed)

    def claim_next(self, worker_id: str, **kwargs: Any) -> Optional[InboxRecord]:
        self._require_open()
        return self.store.claim_next(worker_id, **kwargs)

    def complete_inbox(
        self,
        inbox_id: str,
        claim_token: str,
        *,
        run_id: str,
    ) -> InboxRecord:
        self._require_open()
        return self.store.complete_inbox(
            inbox_id,
            claim_token,
            run_id=run_id,
        )

    def fail_inbox(
        self,
        inbox_id: str,
        claim_token: str,
        *,
        error: str,
        retryable: bool,
    ) -> InboxRecord:
        self._require_open()
        return self.store.fail_inbox(
            inbox_id,
            claim_token,
            error=error,
            retryable=retryable,
        )

    def queue_outbound(
        self,
        envelope: OutboundEnvelope | Mapping[str, Any],
        *,
        idempotency_key: str,
    ) -> tuple[OutboxRecord, bool]:
        self._require_open()
        parsed = (
            envelope
            if isinstance(envelope, OutboundEnvelope)
            else OutboundEnvelope.model_validate(envelope)
        )
        return self.store.queue_outbound(
            parsed,
            idempotency_key=idempotency_key,
        )

    def approve_outbox(self, outbox_id: str) -> OutboxRecord:
        self._require_open()
        return self.store.approve_outbox(outbox_id)

    def reject_outbox(self, outbox_id: str, *, reason: str = "") -> OutboxRecord:
        self._require_open()
        return self.store.reject_outbox(outbox_id, reason=reason)

    def retry_outbox(self, outbox_id: str) -> OutboxRecord:
        self._require_open()
        return self.store.retry_outbox(outbox_id)

    def deliver(self, outbox_id: str) -> OutboxRecord:
        """Attempt one approved delivery and always persist its terminal attempt."""

        self._require_open()
        sending = self.store.begin_delivery(outbox_id)
        assert sending.delivery_token is not None
        adapter = self._adapters.get(sending.envelope.channel)
        try:
            if adapter is None:
                raise ChannelDeliveryError(
                    f"no adapter registered for channel {sending.envelope.channel!r}"
                )
            receipt = adapter.send(
                sending.envelope,
                idempotency_key=sending.idempotency_key,
            )
        except Exception as error:
            return self.store.mark_delivery_failed(
                sending.id,
                sending.delivery_token,
                error=f"{type(error).__name__}: {error}",
            )
        return self.store.mark_delivery_sent(
            sending.id,
            sending.delivery_token,
            receipt,
        )

    def deliver_pending(self, *, limit: Optional[int] = None) -> list[OutboxRecord]:
        self._require_open()
        if limit is not None and limit < 0:
            raise ValueError("limit must be non-negative")
        approved = self.store.list_outbox(OutboxStatus.APPROVED)
        if limit is not None:
            approved = approved[:limit]
        return [self.deliver(record.id) for record in approved]

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        failures = []
        for channel, adapter in reversed(list(self._adapters.items())):
            try:
                adapter.close()
            except Exception as error:
                failures.append(f"{channel}: {type(error).__name__}: {error}")
        if failures:
            raise RuntimeError("failed to close channel adapters: " + "; ".join(failures))

    def _require_open(self) -> None:
        if self._closed:
            raise RuntimeError("durable channel gateway is closed")


def register_channel_tools(
    registry,
    gateway: DurableChannelGateway,
    *,
    idempotency_scope: str,
    tool_prefix: str = "",
) -> list[str]:
    """Register a queue-only send tool with a host-provided Run scope."""

    tool_name = f"{tool_prefix}channel_send" if tool_prefix else "channel_send"

    def channel_send(args: dict[str, Any]) -> str:
        message_key = args.get("message_key", "")
        idempotency_key = stable_tool_idempotency_key(
            idempotency_scope,
            message_key,
        )
        envelope = OutboundEnvelope.model_validate({
            "channel": args.get("channel"),
            "account_id": args.get("account_id"),
            "conversation_id": args.get("conversation_id"),
            "recipient_id": args.get("recipient_id"),
            "content": args.get("content"),
            "reply_to_message_id": args.get("reply_to_message_id"),
        })
        record, created = gateway.queue_outbound(
            envelope,
            idempotency_key=idempotency_key,
        )
        action = "Queued" if created else "Reused"
        return f"{action} outbound message {record.id} [{record.status.value}]"

    registry.register(
        name=tool_name,
        handler=channel_send,
        schema={
            "type": "function",
            "function": {
                "name": tool_name,
                "description": (
                    "Queue an outbound channel message intent. The host must approve "
                    "and deliver it separately. Reuse message_key when retrying the "
                    "same logical send within this run."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "message_key": {
                            "type": "string",
                            "description": "Stable key unique within the current run",
                        },
                        "channel": {"type": "string"},
                        "account_id": {"type": "string"},
                        "conversation_id": {"type": "string"},
                        "recipient_id": {"type": "string"},
                        "content": {"type": "string"},
                        "reply_to_message_id": {"type": "string"},
                    },
                    "required": [
                        "message_key",
                        "channel",
                        "account_id",
                        "conversation_id",
                        "content",
                    ],
                },
            },
        },
    )
    return [tool_name]
