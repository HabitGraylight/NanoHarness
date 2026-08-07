"""Typed NanoHermes jobs, learning proposals, and persistent run state."""

import hashlib
import json
import re
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path, PurePosixPath
from typing import Dict, List, Optional
from uuid import uuid4

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from nanoharness.testing import ArtifactRecord, ScriptedResponse


HERMES_JOB_VERSION = "1.0"
HERMES_STATE_VERSION = "1.0"
LEARNING_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def content_sha256(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def safe_relative_path(value: str) -> str:
    path = PurePosixPath(value.replace("\\", "/"))
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise ValueError(f"path must stay inside the workspace: {value!r}")
    return path.as_posix()


class HermesPhase(str, Enum):
    ASSIST = "assist"
    REFLECT = "reflect"
    REVIEW = "review"
    COMPLETED = "completed"


class HermesStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    BLOCKED = "blocked"
    INTERRUPTED = "interrupted"
    COMPLETED = "completed"


class HermesRunKind(str, Enum):
    USER = "user"
    SCHEDULED = "scheduled"


class ProposalKind(str, Enum):
    MEMORY = "memory"
    SKILL = "skill"


class ProposalStatus(str, Enum):
    STAGED = "staged"
    APPROVED = "approved"
    REJECTED = "rejected"
    INVALID = "invalid"
    PROMOTED = "promoted"


class HermesJob(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = HERMES_JOB_VERSION
    name: str = Field(min_length=1, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    query: str = Field(min_length=1)
    run_kind: HermesRunKind = HermesRunKind.USER
    schedule_id: Optional[int] = Field(default=None, ge=1)
    fixture_files: Dict[str, str] = Field(default_factory=dict)
    phases: Dict[HermesPhase, List[ScriptedResponse]] = Field(default_factory=dict)

    @field_validator("schema_version")
    @classmethod
    def supported_version(cls, value: str) -> str:
        if value != HERMES_JOB_VERSION:
            raise ValueError(
                f"unsupported NanoHermes job version {value!r}; "
                f"expected {HERMES_JOB_VERSION!r}"
            )
        return value

    @field_validator("fixture_files")
    @classmethod
    def safe_fixture_paths(cls, value: Dict[str, str]) -> Dict[str, str]:
        for path in value:
            safe_relative_path(path)
        return value

    @field_validator("phases")
    @classmethod
    def complete_phase_scripts(
        cls,
        value: Dict[HermesPhase, List[ScriptedResponse]],
    ) -> Dict[HermesPhase, List[ScriptedResponse]]:
        if not value:
            return value
        required = {HermesPhase.ASSIST, HermesPhase.REFLECT}
        unsupported = set(value) - required
        if unsupported:
            raise ValueError(
                "unsupported phase scripts: "
                f"{sorted(item.value for item in unsupported)}"
            )
        missing = required - set(value)
        if missing:
            raise ValueError(
                f"missing phase scripts: {sorted(item.value for item in missing)}"
            )
        empty = sorted(phase.value for phase in required if not value[phase])
        if empty:
            raise ValueError(f"phase scripts cannot be empty: {empty}")
        return value

    @model_validator(mode="after")
    def scheduled_run_requires_id(self):
        if self.run_kind == HermesRunKind.SCHEDULED and self.schedule_id is None:
            raise ValueError("scheduled jobs require schedule_id")
        if self.run_kind == HermesRunKind.USER and self.schedule_id is not None:
            raise ValueError("user jobs cannot declare schedule_id")
        return self

    @property
    def scripted(self) -> bool:
        return bool(self.phases)

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
                        f"persistent workspace fixture conflicts with existing path: {relative}"
                    )
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")

    def fingerprint(self) -> str:
        canonical = json.dumps(
            self.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(canonical).hexdigest()

    @classmethod
    def from_file(cls, path: str | Path) -> "HermesJob":
        payload = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("NanoHermes job file must contain a YAML object")
        return cls.model_validate(payload)


class LearningProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    proposal_id: str = Field(default_factory=lambda: f"proposal_{uuid4().hex}")
    kind: ProposalKind
    name: str
    content: str = Field(min_length=1, max_length=50_000)
    description: str = Field(default="", max_length=500)
    memory_type: str = "note"
    trigger: str = Field(default="", max_length=500)
    source_run_id: str
    content_sha256: str = ""
    base_sha256: Optional[str] = None
    staged_path: Optional[str] = None
    status: ProposalStatus = ProposalStatus.STAGED
    validation_error: str = ""
    created_at: datetime = Field(default_factory=utc_now)

    @field_validator("name")
    @classmethod
    def safe_name(cls, value: str) -> str:
        if not LEARNING_NAME.fullmatch(value):
            raise ValueError("learning name contains unsupported characters")
        return value

    @field_validator("memory_type")
    @classmethod
    def valid_memory_type(cls, value: str) -> str:
        if value not in {"note", "feedback", "reference", "project"}:
            raise ValueError("unsupported memory type")
        return value

    @model_validator(mode="after")
    def validate_kind_payload(self):
        digest = content_sha256(self.content)
        if self.content_sha256 and self.content_sha256 != digest:
            raise ValueError("proposal content hash does not match content")
        self.content_sha256 = digest
        if self.kind == ProposalKind.MEMORY and self.trigger:
            raise ValueError("memory proposals cannot declare a trigger")
        return self


class LearningDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    proposal_id: str
    kind: ProposalKind
    name: str
    approved: bool
    reason: str
    content_sha256: str
    at: datetime = Field(default_factory=utc_now)


class ActionApproval(BaseModel):
    model_config = ConfigDict(extra="forbid")

    call_id: str
    tool: str
    approved: bool
    reason: str
    details: Dict[str, str] = Field(default_factory=dict)
    at: datetime = Field(default_factory=utc_now)


class HermesTransition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: HermesPhase
    target: HermesPhase
    reason: str
    at: datetime = Field(default_factory=utc_now)


class HermesRunState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = HERMES_STATE_VERSION
    run_id: str = Field(default_factory=lambda: f"hermes_{uuid4().hex}")
    job_name: str
    job_fingerprint: str
    query: str
    run_kind: HermesRunKind = HermesRunKind.USER
    schedule_id: Optional[int] = None
    phase: HermesPhase = HermesPhase.ASSIST
    status: HermesStatus = HermesStatus.PENDING
    workspace: str
    response: str = ""
    reflection_summary: str = ""
    proposals: List[LearningProposal] = Field(default_factory=list)
    decisions: List[LearningDecision] = Field(default_factory=list)
    action_approvals: List[ActionApproval] = Field(default_factory=list)
    transitions: List[HermesTransition] = Field(default_factory=list)
    artifacts: List[ArtifactRecord] = Field(default_factory=list)
    total_steps: int = 0
    tool_counts: Dict[str, int] = Field(default_factory=dict)
    error: str = ""
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @field_validator("schema_version")
    @classmethod
    def supported_state_version(cls, value: str) -> str:
        if value != HERMES_STATE_VERSION:
            raise ValueError(
                f"unsupported NanoHermes state version {value!r}; "
                f"expected {HERMES_STATE_VERSION!r}"
            )
        return value


class HermesRunResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile: str = "nano-hermes"
    job: str
    run_id: str
    run_kind: HermesRunKind
    status: HermesStatus
    phase: HermesPhase
    success: bool
    response: str
    promoted: List[str] = Field(default_factory=list)
    rejected: List[str] = Field(default_factory=list)
    total_steps: int
    tools: List[str] = Field(default_factory=list)
    action_approvals: List[ActionApproval] = Field(default_factory=list)
    decisions: List[LearningDecision] = Field(default_factory=list)
    artifact: Optional[ArtifactRecord] = None
    artifacts: List[ArtifactRecord] = Field(default_factory=list)
    state_path: str
    workspace: str
    error: str = ""
