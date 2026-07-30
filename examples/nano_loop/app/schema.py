"""Typed contracts for the NanoLoop control plane."""

from enum import Enum
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


class LoopStatus(str, Enum):
    PENDING = "pending"
    PREPARING = "preparing"
    RUNNING = "running"
    VERIFYING = "verifying"
    RETRYING = "retrying"
    WAITING_HUMAN = "waiting_human"
    COMPLETED = "completed"
    BLOCKED = "blocked"
    BUDGET_EXHAUSTED = "budget_exhausted"
    FAILED = "failed"


class BudgetSpec(BaseModel):
    max_iterations: int = Field(default=3, ge=1)
    max_wall_seconds: int = Field(default=1800, ge=1)
    max_consecutive_failures: int = Field(default=2, ge=1)


class WorkerSpec(BaseModel):
    type: Literal["nano_engine"] = "nano_engine"
    model: str = "deepseek-chat"
    base_url: str = "https://api.deepseek.com"
    max_steps: int = Field(default=20, ge=1)


class WorkspaceSpec(BaseModel):
    type: Literal["git_worktree", "local"] = "git_worktree"
    base_ref: str = "HEAD"


class VerifySpec(BaseModel):
    commands: List[str] = Field(default_factory=list)
    timeout_seconds: int = Field(default=300, ge=1)
    max_output_chars: int = Field(default=12000, ge=256)


class GateSpec(BaseModel):
    require_human: List[str] = Field(default_factory=list)


class LoopSpec(BaseModel):
    name: str
    goal: str
    worker: WorkerSpec = Field(default_factory=WorkerSpec)
    workspace: WorkspaceSpec = Field(default_factory=WorkspaceSpec)
    verify: VerifySpec = Field(default_factory=VerifySpec)
    budget: BudgetSpec = Field(default_factory=BudgetSpec)
    gates: GateSpec = Field(default_factory=GateSpec)


class WorkspaceHandle(BaseModel):
    path: str
    branch: str = ""
    owned: bool = False


class Evidence(BaseModel):
    kind: str
    passed: bool
    summary: str
    command: Optional[str] = None
    exit_code: Optional[int] = None
    output: str = ""
    metadata: Dict[str, Any] = Field(default_factory=dict)


class VerificationResult(BaseModel):
    passed: bool
    evidence: List[Evidence] = Field(default_factory=list)
    feedback: str = ""


class WorkerResult(BaseModel):
    success: bool
    summary: str
    report: Dict[str, Any] = Field(default_factory=dict)


class IterationRecord(BaseModel):
    number: int
    status: str = "running"
    started_at: float
    completed_at: Optional[float] = None
    worker: Optional[WorkerResult] = None
    verification: Optional[VerificationResult] = None
    error: str = ""


class LoopState(BaseModel):
    run_id: str
    spec: LoopSpec
    task: str
    repository: str
    status: LoopStatus = LoopStatus.PENDING
    workspace: Optional[WorkspaceHandle] = None
    iterations: List[IterationRecord] = Field(default_factory=list)
    feedback: str = ""
    consecutive_failures: int = 0
    pending_gates: List[str] = Field(default_factory=list)
    started_at: float
    updated_at: float
    completed_at: Optional[float] = None
    stop_reason: str = ""

    @property
    def iteration_count(self) -> int:
        return len(self.iterations)

    @property
    def is_terminal(self) -> bool:
        return self.status in {
            LoopStatus.COMPLETED,
            LoopStatus.BLOCKED,
            LoopStatus.BUDGET_EXHAUSTED,
            LoopStatus.FAILED,
        }
