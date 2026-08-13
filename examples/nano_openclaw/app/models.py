"""Typed jobs, routes, turns, approvals, and results for NanoOpenClaw."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path, PurePosixPath
from typing import Dict, List, Literal, Optional

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


class GatewayJob(_StrictModel):
    schema_version: Literal["1.0"] = OPENCLAW_JOB_VERSION
    name: str = Field(min_length=1, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    description: str = ""
    fixture_files: Dict[str, str] = Field(default_factory=dict)
    messages: List[GatewayJobMessage] = Field(min_length=1)

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
        return self

    @property
    def scripted(self) -> bool:
        return all(message.responses for message in self.messages)

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

    @classmethod
    def from_file(cls, path: str | Path) -> "GatewayJob":
        payload = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("NanoOpenClaw job file must contain a YAML object")
        return cls.model_validate(payload)


class ConversationRoute(_StrictModel):
    channel: str
    account_id: str
    conversation_id: str
    sender_id: str

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


class ConversationExchange(_StrictModel):
    turn_id: str
    inbox_id: str
    external_message_id: str
    user_content: str
    assistant_content: str
    delivered: bool
    outbox_id: Optional[str] = None
    completed_at: datetime = Field(default_factory=utc_now)


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
    inbox_id: str
    claim_token: Optional[str] = None
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
        if self.run_id != stable_turn_id(self.inbox_id):
            raise ValueError("turn run id does not match inbox id")
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
    inbox_id: str
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
