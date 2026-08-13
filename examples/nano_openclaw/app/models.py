"""Typed jobs, routes, turns, approvals, and results for NanoOpenClaw."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path, PurePosixPath
from typing import Any, Dict, List, Literal, Optional

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from nanoharness.extensions.channels import InboundEnvelope, OutboxStatus
from nanoharness.testing import ArtifactRecord, ScriptedResponse


OPENCLAW_JOB_VERSION = "1.0"
OPENCLAW_STATE_VERSION = "1.0"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def content_sha256(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _stable_id(prefix: str, value: str) -> str:
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]
    return f"{prefix}_{digest}"


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GatewayJobMessage(_StrictModel):
    envelope: InboundEnvelope
    responses: List[ScriptedResponse] = Field(default_factory=list)

    def fingerprint(self) -> str:
        envelope = self.envelope.model_dump(
            mode="json",
            exclude={"received_at"},
        )
        payload = json.dumps(
            {
                "envelope": envelope,
                "responses": [item.model_dump(mode="json") for item in self.responses],
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return content_sha256(payload)


class GatewayJobSchedule(_StrictModel):
    name: str = Field(min_length=1, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    prompt: str = Field(min_length=1, max_length=100_000)
    channel: str = "mock"
    account_id: str
    conversation_id: str
    sender_id: str
    cron: Optional[str] = None
    delay_seconds: Optional[int] = Field(default=None, ge=0)
    max_fires: Optional[int] = Field(default=None, ge=1)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    responses: List[ScriptedResponse] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_timing(self):
        if not self.cron and self.delay_seconds is None:
            raise ValueError("scheduled wakeup requires cron or delay_seconds")
        if self.cron and len(self.cron.strip().split()) != 5:
            raise ValueError("cron must contain five fields")
        _canonical_json(self.metadata)
        _ = self.route
        return self

    @property
    def route(self) -> "ConversationRoute":
        return ConversationRoute(
            channel=self.channel,
            account_id=self.account_id,
            conversation_id=self.conversation_id,
            sender_id=self.sender_id,
        )


class GatewayJob(_StrictModel):
    schema_version: Literal["1.0"] = OPENCLAW_JOB_VERSION
    name: str = Field(min_length=1, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    description: str = ""
    fixture_files: Dict[str, str] = Field(default_factory=dict)
    messages: List[GatewayJobMessage] = Field(default_factory=list)
    schedules: List[GatewayJobSchedule] = Field(default_factory=list)

    @field_validator("fixture_files")
    @classmethod
    def safe_fixture_paths(cls, value: Dict[str, str]) -> Dict[str, str]:
        for raw_path in value:
            path = PurePosixPath(raw_path.replace("\\", "/"))
            if path.is_absolute() or ".." in path.parts or not path.parts:
                raise ValueError(
                    f"fixture path must stay inside the workspace: {raw_path!r}"
                )
        return value

    @model_validator(mode="after")
    def unique_message_identities(self):
        identities = [message.envelope.dedupe_key for message in self.messages]
        if len(identities) != len(set(identities)):
            raise ValueError("job messages must have unique channel identities")
        schedule_names = [schedule.name for schedule in self.schedules]
        if len(schedule_names) != len(set(schedule_names)):
            raise ValueError("job schedules must have unique names")
        return self

    @property
    def scripted(self) -> bool:
        scripted_inputs = [
            *(message.responses for message in self.messages),
            *(schedule.responses for schedule in self.schedules),
        ]
        return bool(scripted_inputs) and all(scripted_inputs)

    def materialize(self, workspace: Path) -> None:
        root = workspace.resolve()
        root.mkdir(parents=True, exist_ok=True)
        for relative, content in self.fixture_files.items():
            target = (root / PurePosixPath(relative)).resolve()
            if target != root and root not in target.parents:
                raise ValueError(f"fixture path escapes workspace: {relative!r}")
            if target.exists():
                if not target.is_file() or target.read_text(encoding="utf-8") != content:
                    raise ValueError(
                        "persistent workspace fixture conflicts with existing path: "
                        f"{relative}"
                    )
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")

    def message_for(self, envelope: InboundEnvelope) -> Optional[GatewayJobMessage]:
        return next(
            (
                message
                for message in self.messages
                if message.envelope.dedupe_key == envelope.dedupe_key
            ),
            None,
        )

    def schedule_for(self, name: str) -> Optional[GatewayJobSchedule]:
        return next(
            (schedule for schedule in self.schedules if schedule.name == name),
            None,
        )

    @classmethod
    def from_file(cls, path: str | Path) -> "GatewayJob":
        payload = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("NanoOpenClaw job file must contain a YAML object")
        return cls.model_validate(payload)


class ConversationRoute(_StrictModel):
    channel: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
    account_id: str = Field(min_length=1, max_length=256)
    conversation_id: str = Field(min_length=1, max_length=256)
    sender_id: str = Field(min_length=1, max_length=256)

    @field_validator("account_id", "conversation_id", "sender_id")
    @classmethod
    def validate_identifier(cls, value: str) -> str:
        if value != value.strip():
            raise ValueError("route identifier cannot have surrounding whitespace")
        if any(ord(character) < 32 or ord(character) == 127 for character in value):
            raise ValueError("route identifier cannot contain control characters")
        return value

    @classmethod
    def from_envelope(cls, envelope: InboundEnvelope) -> "ConversationRoute":
        return cls(
            channel=envelope.channel,
            account_id=envelope.account_id,
            conversation_id=envelope.conversation_id,
            sender_id=envelope.sender_id,
        )

    @property
    def key(self) -> str:
        return json.dumps(
            [self.channel, self.account_id, self.conversation_id, self.sender_id],
            ensure_ascii=False,
            separators=(",", ":"),
        )

    @property
    def stable_conversation_id(self) -> str:
        return _stable_id("conversation", self.key)

    @property
    def stable_session_id(self) -> str:
        return _stable_id("session", self.key)


class WakeupSource(str, Enum):
    CHANNEL = "channel"
    SCHEDULE = "schedule"
    BACKGROUND = "background"
    MANUAL = "manual"


class WakeupTrust(str, Enum):
    UNTRUSTED = "untrusted"
    TRUSTED_SYSTEM = "trusted_system"
    OPERATOR = "operator"


class WakeupStatus(str, Enum):
    PENDING = "pending"
    CLAIMED = "claimed"
    COMPLETED = "completed"
    FAILED = "failed"


def _expected_trust(source: WakeupSource) -> WakeupTrust:
    return {
        WakeupSource.CHANNEL: WakeupTrust.UNTRUSTED,
        WakeupSource.SCHEDULE: WakeupTrust.TRUSTED_SYSTEM,
        WakeupSource.BACKGROUND: WakeupTrust.TRUSTED_SYSTEM,
        WakeupSource.MANUAL: WakeupTrust.OPERATOR,
    }[source]


class WakeupEnvelope(_StrictModel):
    schema_version: Literal["1.0"] = OPENCLAW_STATE_VERSION
    wakeup_id: str
    source: WakeupSource
    trust: WakeupTrust
    source_id: str = Field(min_length=1, max_length=512)
    route: ConversationRoute
    content: str = Field(min_length=1, max_length=100_000)
    channel_inbox_id: Optional[str] = None
    occurred_at: datetime = Field(default_factory=utc_now)
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("source_id")
    @classmethod
    def validate_source_id(cls, value: str) -> str:
        if value != value.strip():
            raise ValueError("source_id cannot have surrounding whitespace")
        if any(ord(character) < 32 or ord(character) == 127 for character in value):
            raise ValueError("source_id cannot contain control characters")
        return value

    @field_validator("content")
    @classmethod
    def validate_content(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("wakeup content cannot be blank")
        return value

    @field_validator("occurred_at")
    @classmethod
    def validate_occurred_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("occurred_at must include a timezone")
        return value.astimezone(timezone.utc)

    @model_validator(mode="after")
    def validate_source_identity(self):
        expected_trust = _expected_trust(self.source)
        if self.trust != expected_trust:
            raise ValueError(
                f"{self.source.value} wakeups require {expected_trust.value} trust"
            )
        expected_id = stable_wakeup_id(self.source, self.source_id)
        if self.wakeup_id != expected_id:
            raise ValueError("wakeup id does not match source identity")
        if self.source == WakeupSource.CHANNEL:
            if not self.channel_inbox_id or self.channel_inbox_id != self.source_id:
                raise ValueError("channel wakeups require their inbox identity")
        elif self.channel_inbox_id is not None:
            raise ValueError("non-channel wakeups cannot reference a channel inbox")
        _canonical_json(self.metadata)
        return self

    @property
    def payload_fingerprint(self) -> str:
        payload = self.model_dump(mode="json", exclude={"occurred_at"})
        return content_sha256(_canonical_json(payload))


class WakeupRecord(_StrictModel):
    envelope: WakeupEnvelope
    payload_fingerprint: str
    status: WakeupStatus = WakeupStatus.PENDING
    claim_token: Optional[str] = None
    claim_owner: Optional[str] = None
    claimed_at: Optional[datetime] = None
    lease_expires_at: Optional[datetime] = None
    claim_count: int = Field(default=0, ge=0)
    run_id: Optional[str] = None
    last_error: str = ""
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @property
    def id(self) -> str:
        return self.envelope.wakeup_id

    @model_validator(mode="after")
    def validate_record(self):
        if self.payload_fingerprint != self.envelope.payload_fingerprint:
            raise ValueError("wakeup payload fingerprint does not match envelope")
        claim_values = (
            self.claim_token,
            self.claim_owner,
            self.claimed_at,
            self.lease_expires_at,
        )
        if self.status == WakeupStatus.CLAIMED:
            if any(value is None for value in claim_values):
                raise ValueError("claimed wakeups require complete lease state")
        elif any(value is not None for value in claim_values):
            raise ValueError("unclaimed wakeups cannot retain lease state")
        if self.status == WakeupStatus.COMPLETED and not self.run_id:
            raise ValueError("completed wakeups require run_id")
        return self


class ConversationExchange(_StrictModel):
    turn_id: str
    wakeup_id: Optional[str] = None
    inbox_id: Optional[str] = None
    source: WakeupSource = WakeupSource.CHANNEL
    trust: WakeupTrust = WakeupTrust.UNTRUSTED
    external_message_id: str
    user_content: str
    assistant_content: str
    delivered: bool
    outbox_id: Optional[str] = None
    completed_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def validate_trust(self):
        if self.trust != _expected_trust(self.source):
            raise ValueError("conversation exchange trust does not match its source")
        return self


class ConversationState(_StrictModel):
    schema_version: Literal["1.0"] = OPENCLAW_STATE_VERSION
    conversation_id: str
    session_id: str
    route: ConversationRoute
    exchanges: List[ConversationExchange] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def validate_identity(self):
        if self.conversation_id != self.route.stable_conversation_id:
            raise ValueError("conversation id does not match its route")
        if self.session_id != self.route.stable_session_id:
            raise ValueError("session id does not match its route")
        if len({exchange.turn_id for exchange in self.exchanges}) != len(
            self.exchanges
        ):
            raise ValueError("conversation exchanges must have unique turn ids")
        return self


class TurnPhase(str, Enum):
    RESPOND = "respond"
    DELIVERY = "delivery"
    COMPLETED = "completed"


class TurnStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    BLOCKED = "blocked"
    INTERRUPTED = "interrupted"
    WAITING = "waiting"
    COMPLETED = "completed"


class OutboundApproval(_StrictModel):
    outbox_id: str
    approved: bool
    reason: str
    channel: str
    account_id: str
    conversation_id: str
    recipient_id: Optional[str] = None
    content_sha256: str
    content_length: int = Field(ge=1)
    at: datetime = Field(default_factory=utc_now)


class GatewayTurnState(_StrictModel):
    schema_version: Literal["1.0"] = OPENCLAW_STATE_VERSION
    run_id: str
    job_name: str
    message_fingerprint: str
    wakeup_id: Optional[str] = None
    inbox_id: Optional[str] = None
    source: WakeupSource = WakeupSource.CHANNEL
    trust: WakeupTrust = WakeupTrust.UNTRUSTED
    claim_token: Optional[str] = None
    wakeup_claim_token: Optional[str] = None
    route: ConversationRoute
    conversation_id: str
    session_id: str
    external_message_id: str
    user_content: str
    workspace: str
    phase: TurnPhase = TurnPhase.RESPOND
    status: TurnStatus = TurnStatus.PENDING
    response: str = ""
    outbox_id: Optional[str] = None
    delivery_status: Optional[OutboxStatus] = None
    approval: Optional[OutboundApproval] = None
    engine_attempts: int = Field(default=0, ge=0)
    total_steps: int = Field(default=0, ge=0)
    tool_counts: Dict[str, int] = Field(default_factory=dict)
    artifacts: List[ArtifactRecord] = Field(default_factory=list)
    error: str = ""
    completed_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def validate_turn(self):
        if self.wakeup_id is None:
            self.wakeup_id = self.inbox_id
        if not self.wakeup_id:
            raise ValueError("turn requires a wakeup id")
        if self.source == WakeupSource.CHANNEL and not self.inbox_id:
            raise ValueError("channel turns require an inbox id")
        if self.source != WakeupSource.CHANNEL and self.inbox_id is not None:
            raise ValueError("non-channel turns cannot reference an inbox")
        if self.trust != _expected_trust(self.source):
            raise ValueError("turn trust does not match its wakeup source")
        if self.run_id != stable_turn_id(self.wakeup_id):
            raise ValueError("turn run id does not match wakeup id")
        if self.conversation_id != self.route.stable_conversation_id:
            raise ValueError("turn conversation id does not match route")
        if self.session_id != self.route.stable_session_id:
            raise ValueError("turn session id does not match route")
        if self.phase != TurnPhase.RESPOND and not self.response:
            raise ValueError("delivery and completed turns require a response")
        if self.status == TurnStatus.COMPLETED and self.phase != TurnPhase.COMPLETED:
            raise ValueError("completed status requires completed phase")
        return self


class GatewayRunResult(_StrictModel):
    profile: str = "nano-openclaw"
    job: str
    run_id: str
    wakeup_id: str
    inbox_id: Optional[str] = None
    source: WakeupSource
    trust: WakeupTrust
    conversation_id: str
    session_id: str
    status: TurnStatus
    phase: TurnPhase
    success: bool
    response: str
    delivery_status: Optional[OutboxStatus] = None
    outbox_id: Optional[str] = None
    approval: Optional[OutboundApproval] = None
    total_steps: int = 0
    tools: List[str] = Field(default_factory=list)
    artifact: Optional[ArtifactRecord] = None
    artifacts: List[ArtifactRecord] = Field(default_factory=list)
    state_path: str
    conversation_path: str
    workspace: str
    error: str = ""


class GatewayBatchResult(_StrictModel):
    profile: str = "nano-openclaw"
    job: str
    success: bool
    processed: int
    delivered: int
    turns: List[GatewayRunResult]


def stable_turn_id(inbox_id: str) -> str:
    return _stable_id("openclaw", inbox_id)


def stable_wakeup_id(source: WakeupSource | str, source_id: str) -> str:
    parsed = WakeupSource(source)
    if parsed == WakeupSource.CHANNEL:
        return source_id
    return _stable_id(f"wake_{parsed.value}", source_id)


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
