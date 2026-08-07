"""Strict, transport-neutral models for durable channel gateways."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from enum import Enum
from typing import Annotated, Any, Dict, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


CHANNEL_SCHEMA_VERSION = "1.0"

ChannelName = Annotated[
    str,
    Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$"),
]
ExternalId = Annotated[str, Field(min_length=1, max_length=256)]
Content = Annotated[str, Field(min_length=1, max_length=100_000)]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class InboxStatus(str, Enum):
    RECEIVED = "received"
    CLAIMED = "claimed"
    COMPLETED = "completed"
    FAILED = "failed"


class OutboxStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    SENDING = "sending"
    SENT = "sent"
    FAILED = "failed"


class DeliveryAttemptStatus(str, Enum):
    SENDING = "sending"
    SENT = "sent"
    FAILED = "failed"
    RECOVERED = "recovered"


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class InboundEnvelope(_StrictModel):
    """One normalized untrusted user message from a channel adapter.

    There is deliberately no role field: channel ingress cannot inject system or
    tool messages. The host decides how this user content enters a prompt.
    """

    schema_version: Literal["1.0"] = CHANNEL_SCHEMA_VERSION
    message_id: ExternalId
    channel: ChannelName
    account_id: ExternalId
    conversation_id: ExternalId
    sender_id: ExternalId
    content: Content
    received_at: datetime = Field(default_factory=utc_now)
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @field_validator(
        "message_id",
        "account_id",
        "conversation_id",
        "sender_id",
    )
    @classmethod
    def validate_identifier(cls, value: str) -> str:
        return _validate_external_id(value)

    @field_validator("content", mode="before")
    @classmethod
    def validate_content(cls, value: Any) -> Any:
        if isinstance(value, str) and not value.strip():
            raise ValueError("content cannot be blank")
        return value

    @field_validator("received_at")
    @classmethod
    def validate_received_at(cls, value: datetime) -> datetime:
        return _require_aware_datetime(value, "received_at")

    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, value: Dict[str, Any]) -> Dict[str, Any]:
        _canonical_json(value)
        return value

    @property
    def dedupe_key(self) -> str:
        return _canonical_json([self.channel, self.account_id, self.message_id])

    @property
    def payload_fingerprint(self) -> str:
        payload = self.model_dump(mode="json", exclude={"received_at"})
        return _sha256(_canonical_json(payload))


class OutboundEnvelope(_StrictModel):
    """A transport-neutral outbound message intent."""

    schema_version: Literal["1.0"] = CHANNEL_SCHEMA_VERSION
    channel: ChannelName
    account_id: ExternalId
    conversation_id: ExternalId
    recipient_id: Optional[ExternalId] = None
    content: Content
    reply_to_message_id: Optional[ExternalId] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @field_validator(
        "account_id",
        "conversation_id",
        "recipient_id",
        "reply_to_message_id",
    )
    @classmethod
    def validate_identifier(cls, value: Optional[str]) -> Optional[str]:
        return None if value is None else _validate_external_id(value)

    @field_validator("content", mode="before")
    @classmethod
    def validate_content(cls, value: Any) -> Any:
        if isinstance(value, str) and not value.strip():
            raise ValueError("content cannot be blank")
        return value

    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, value: Dict[str, Any]) -> Dict[str, Any]:
        _canonical_json(value)
        return value

    @property
    def payload_fingerprint(self) -> str:
        return _sha256(_canonical_json(self.model_dump(mode="json")))


class InboxRecord(_StrictModel):
    id: str
    dedupe_key: str
    payload_fingerprint: str
    envelope: InboundEnvelope
    status: InboxStatus = InboxStatus.RECEIVED
    claim_token: Optional[str] = None
    claim_owner: Optional[str] = None
    claimed_at: Optional[datetime] = None
    lease_expires_at: Optional[datetime] = None
    claim_count: int = Field(default=0, ge=0)
    run_id: Optional[str] = None
    last_error: Optional[str] = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @field_validator(
        "claimed_at",
        "lease_expires_at",
        "created_at",
        "updated_at",
    )
    @classmethod
    def validate_timestamp(cls, value: Optional[datetime]) -> Optional[datetime]:
        return None if value is None else _require_aware_datetime(value, "timestamp")

    @model_validator(mode="after")
    def validate_state(self):
        claimed_values = (
            self.claim_token,
            self.claim_owner,
            self.claimed_at,
            self.lease_expires_at,
        )
        if self.status == InboxStatus.CLAIMED:
            if any(value is None for value in claimed_values):
                raise ValueError("claimed inbox records require complete lease state")
        elif any(value is not None for value in claimed_values):
            raise ValueError("unclaimed inbox records cannot retain lease state")
        if self.status == InboxStatus.COMPLETED and not self.run_id:
            raise ValueError("completed inbox records require run_id")
        if self.id != stable_inbox_id(self.envelope):
            raise ValueError("inbox id does not match envelope identity")
        if self.dedupe_key != self.envelope.dedupe_key:
            raise ValueError("inbox dedupe key does not match envelope")
        if self.payload_fingerprint != self.envelope.payload_fingerprint:
            raise ValueError("inbox payload fingerprint does not match envelope")
        return self


class DeliveryAttempt(_StrictModel):
    number: int = Field(ge=1)
    token: str
    status: DeliveryAttemptStatus = DeliveryAttemptStatus.SENDING
    started_at: datetime = Field(default_factory=utc_now)
    finished_at: Optional[datetime] = None
    error: Optional[str] = None
    external_delivery_id: Optional[str] = None

    @field_validator("started_at", "finished_at")
    @classmethod
    def validate_timestamp(cls, value: Optional[datetime]) -> Optional[datetime]:
        return None if value is None else _require_aware_datetime(value, "timestamp")


class OutboxRecord(_StrictModel):
    id: str
    idempotency_key: str
    payload_fingerprint: str
    envelope: OutboundEnvelope
    status: OutboxStatus = OutboxStatus.PENDING
    delivery_token: Optional[str] = None
    attempts: list[DeliveryAttempt] = Field(default_factory=list)
    last_error: Optional[str] = None
    external_delivery_id: Optional[str] = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @field_validator("created_at", "updated_at")
    @classmethod
    def validate_timestamp(cls, value: datetime) -> datetime:
        return _require_aware_datetime(value, "timestamp")

    @model_validator(mode="after")
    def validate_state(self):
        if self.id != stable_outbox_id(self.idempotency_key):
            raise ValueError("outbox id does not match idempotency key")
        if self.payload_fingerprint != self.envelope.payload_fingerprint:
            raise ValueError("outbox payload fingerprint does not match envelope")
        if self.status == OutboxStatus.SENDING:
            if not self.delivery_token or not self.attempts:
                raise ValueError("sending outbox records require an active attempt")
            if self.attempts[-1].token != self.delivery_token:
                raise ValueError("delivery token does not match active attempt")
            if self.attempts[-1].status != DeliveryAttemptStatus.SENDING:
                raise ValueError("active attempt must be sending")
        elif self.delivery_token is not None:
            raise ValueError("non-sending outbox records cannot retain delivery token")
        for index, attempt in enumerate(self.attempts, start=1):
            if attempt.number != index:
                raise ValueError("delivery attempt numbers must be contiguous")
        if self.status == OutboxStatus.SENT and not self.external_delivery_id:
            raise ValueError("sent outbox records require external_delivery_id")
        return self


class DeliveryReceipt(_StrictModel):
    channel: ChannelName
    idempotency_key: str
    external_delivery_id: str
    delivered_at: datetime = Field(default_factory=utc_now)
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("idempotency_key", "external_delivery_id")
    @classmethod
    def validate_identifier(cls, value: str) -> str:
        return _validate_external_id(value, max_length=512)

    @field_validator("delivered_at")
    @classmethod
    def validate_delivered_at(cls, value: datetime) -> datetime:
        return _require_aware_datetime(value, "delivered_at")

    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, value: Dict[str, Any]) -> Dict[str, Any]:
        _canonical_json(value)
        return value


class ChannelStoreState(_StrictModel):
    schema_version: Literal["1.0"] = CHANNEL_SCHEMA_VERSION
    inbox: Dict[str, InboxRecord] = Field(default_factory=dict)
    outbox: Dict[str, OutboxRecord] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_indexes(self):
        if any(key != record.id for key, record in self.inbox.items()):
            raise ValueError("inbox index key does not match record id")
        if any(key != record.id for key, record in self.outbox.items()):
            raise ValueError("outbox index key does not match record id")
        return self


def stable_inbox_id(envelope: InboundEnvelope) -> str:
    return "in_" + _sha256(envelope.dedupe_key)[:24]


def stable_outbox_id(idempotency_key: str) -> str:
    return "out_" + _sha256(_validate_external_id(idempotency_key, max_length=512))[:24]


def stable_tool_idempotency_key(
    scope_id: str,
    message_key: str,
) -> str:
    scope = _validate_external_id(scope_id, max_length=512)
    message = _validate_external_id(message_key, max_length=256)
    digest = _sha256(f"{scope}\0{message}")
    return f"tool_{digest}"


def _validate_external_id(value: str, *, max_length: int = 256) -> str:
    if not value or len(value) > max_length:
        raise ValueError(f"identifier must contain 1 to {max_length} characters")
    if value != value.strip():
        raise ValueError("identifier cannot have surrounding whitespace")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError("identifier cannot contain control characters")
    return value


def _require_aware_datetime(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must include a timezone")
    return value.astimezone(timezone.utc)


def _canonical_json(value: Any) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as error:
        raise ValueError("metadata must contain finite JSON values") from error


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
