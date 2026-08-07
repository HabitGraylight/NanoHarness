"""Typed NanoCodex job, phase, evidence, and run-state contracts."""

import hashlib
import json
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path, PurePosixPath
from typing import Dict, List, Literal, Optional
from uuid import uuid4

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from nanoharness.testing import ArtifactRecord, ScriptedResponse


CODEX_JOB_VERSION = "1.0"
CODEX_STATE_VERSION = "1.0"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class CodexPhase(str, Enum):
    PLAN = "plan"
    EXECUTE = "execute"
    REVIEW = "review"
    COMPLETED = "completed"


class CodexStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    BLOCKED = "blocked"
    INTERRUPTED = "interrupted"
    COMPLETED = "completed"


class EvidenceKind(str, Enum):
    FILE_EXISTS = "file_exists"
    FILE_CONTAINS = "file_contains"
    COMMAND = "command"


class DeliveryMode(str, Enum):
    KEEP = "keep"
    COMMIT = "commit"
    APPLY = "apply"
    MERGE = "merge"


class DeliveryStatus(str, Enum):
    NONE = "none"
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"


def _safe_relative_path(value: str) -> str:
    path = PurePosixPath(value.replace("\\", "/"))
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise ValueError(f"path must stay inside the workspace: {value!r}")
    return path.as_posix()


class EvidenceCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: EvidenceKind
    path: Optional[str] = None
    contains: Optional[str] = None
    command: List[str] = Field(default_factory=list)
    timeout_seconds: int = Field(default=30, ge=1, le=3600)

    @field_validator("path")
    @classmethod
    def safe_path(cls, value: Optional[str]) -> Optional[str]:
        return _safe_relative_path(value) if value is not None else None

    @model_validator(mode="after")
    def validate_payload(self):
        if self.kind in {EvidenceKind.FILE_EXISTS, EvidenceKind.FILE_CONTAINS}:
            if not self.path:
                raise ValueError(f"{self.kind.value} evidence requires path")
            if self.command:
                raise ValueError("file evidence cannot declare command")
        if self.kind == EvidenceKind.FILE_CONTAINS and self.contains is None:
            raise ValueError("file_contains evidence requires contains")
        if self.kind == EvidenceKind.COMMAND:
            if not self.command:
                raise ValueError("command evidence requires a non-empty command")
            if self.path is not None or self.contains is not None:
                raise ValueError("command evidence cannot declare path or contains")
        return self


class TrustedCommand(BaseModel):
    """Host-configured command selectable by name, never supplied as raw shell."""

    model_config = ConfigDict(extra="forbid")

    argv: List[str] = Field(min_length=1)
    description: str = ""
    timeout_seconds: int = Field(default=120, ge=1, le=3600)

    @field_validator("argv")
    @classmethod
    def non_empty_argv(cls, value: List[str]) -> List[str]:
        if any(not str(item).strip() for item in value):
            raise ValueError("command argv items cannot be empty")
        return [str(item) for item in value]


class CodexJob(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = CODEX_JOB_VERSION
    name: str = Field(min_length=1, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    objective: str = Field(min_length=1)
    fixture_files: Dict[str, str] = Field(default_factory=dict)
    phases: Dict[CodexPhase, List[ScriptedResponse]] = Field(default_factory=dict)
    commands: Dict[str, TrustedCommand] = Field(default_factory=dict)
    allowed_deliveries: List[DeliveryMode] = Field(
        default_factory=lambda: [DeliveryMode.KEEP, DeliveryMode.COMMIT]
    )
    evidence: List[EvidenceCheck] = Field(min_length=1)

    @field_validator("schema_version")
    @classmethod
    def supported_version(cls, value: str) -> str:
        if value != CODEX_JOB_VERSION:
            raise ValueError(
                f"unsupported NanoCodex job version {value!r}; "
                f"expected {CODEX_JOB_VERSION!r}"
            )
        return value

    @field_validator("fixture_files")
    @classmethod
    def safe_fixture_paths(cls, value: Dict[str, str]) -> Dict[str, str]:
        for path in value:
            _safe_relative_path(path)
        return value

    @field_validator("phases")
    @classmethod
    def complete_phase_scripts(
        cls,
        value: Dict[CodexPhase, List[ScriptedResponse]],
    ) -> Dict[CodexPhase, List[ScriptedResponse]]:
        if not value:
            return value
        required = {CodexPhase.PLAN, CodexPhase.EXECUTE, CodexPhase.REVIEW}
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

    @field_validator("commands")
    @classmethod
    def safe_command_names(
        cls,
        value: Dict[str, TrustedCommand],
    ) -> Dict[str, TrustedCommand]:
        for name in value:
            if not name or not all(char.isalnum() or char in "_.-" for char in name):
                raise ValueError(f"unsafe command name: {name!r}")
        return value

    @field_validator("allowed_deliveries")
    @classmethod
    def valid_deliveries(cls, value: List[DeliveryMode]) -> List[DeliveryMode]:
        if not value:
            raise ValueError("allowed_deliveries cannot be empty")
        if len(value) != len(set(value)):
            raise ValueError("allowed_deliveries cannot contain duplicates")
        return value

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
    def from_file(cls, path: str | Path) -> "CodexJob":
        source = Path(path)
        payload = yaml.safe_load(source.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("NanoCodex job file must contain a YAML object")
        return cls.model_validate(payload)


class PhaseTransition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: CodexPhase
    target: CodexPhase
    reason: str
    at: datetime = Field(default_factory=utc_now)


class ApprovalRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    call_id: str
    tool: str
    approved: bool
    reason: str
    path: Optional[str] = None
    details: Dict[str, str] = Field(default_factory=dict)
    at: datetime = Field(default_factory=utc_now)


class EvidenceRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: EvidenceKind
    passed: bool
    description: str
    exit_code: Optional[int] = None


class CodexRunState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = CODEX_STATE_VERSION
    run_id: str = Field(default_factory=lambda: f"codex_{uuid4().hex}")
    job_name: str
    job_fingerprint: str
    objective: str
    phase: CodexPhase = CodexPhase.PLAN
    status: CodexStatus = CodexStatus.PENDING
    repository: str
    source_repository: Optional[str] = None
    source_head: Optional[str] = None
    active_workspace: Optional[str] = None
    root_task_id: Optional[int] = None
    step_task_ids: List[int] = Field(default_factory=list)
    plan_steps: List[str] = Field(default_factory=list)
    worktree_name: Optional[str] = None
    changed_files: List[str] = Field(default_factory=list)
    execution_summary: str = ""
    agent_review: Optional[Literal["pass", "fail"]] = None
    review_findings: List[str] = Field(default_factory=list)
    delivery_mode: Optional[DeliveryMode] = None
    delivery_status: DeliveryStatus = DeliveryStatus.NONE
    delivery_commit: Optional[str] = None
    delivery_target_commit: Optional[str] = None
    delivery_error: str = ""
    transitions: List[PhaseTransition] = Field(default_factory=list)
    approvals: List[ApprovalRecord] = Field(default_factory=list)
    evidence: List[EvidenceRecord] = Field(default_factory=list)
    artifacts: List[ArtifactRecord] = Field(default_factory=list)
    total_steps: int = 0
    tool_counts: Dict[str, int] = Field(default_factory=dict)
    error: str = ""
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @field_validator("schema_version")
    @classmethod
    def supported_state_version(cls, value: str) -> str:
        if value != CODEX_STATE_VERSION:
            raise ValueError(
                f"unsupported NanoCodex state version {value!r}; "
                f"expected {CODEX_STATE_VERSION!r}"
            )
        return value


class CodexRunResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile: str = "nano-codex"
    job: str
    run_id: str
    status: CodexStatus
    phase: CodexPhase
    success: bool
    total_steps: int
    tools: List[str] = Field(default_factory=list)
    approvals: List[ApprovalRecord] = Field(default_factory=list)
    evidence: List[EvidenceRecord] = Field(default_factory=list)
    artifact: Optional[ArtifactRecord] = None
    state_path: str
    repository: str
    active_workspace: Optional[str] = None
    delivery_mode: Optional[DeliveryMode] = None
    delivery_commit: Optional[str] = None
    delivery_target_commit: Optional[str] = None
    error: str = ""
