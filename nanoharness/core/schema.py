"""Typed contracts shared by the NanoHarness core.

Protocol v2 keeps the v1 string-oriented report surface intact while adding
run identity, structured events, and lossless multi-tool execution traces.
"""

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional
from uuid import uuid4

from pydantic import BaseModel, Field


CORE_PROTOCOL_VERSION = "2.0"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


# ── LLM interaction ──


class ToolCall(BaseModel):
    """A model-requested tool invocation.

    ``call_id`` is optional at provider boundaries. The engine assigns a
    stable ID before the call is placed in context or executed.
    """

    name: str
    arguments: Dict[str, Any]
    call_id: Optional[str] = None

    def model_dump(self, *args, **kwargs):
        # Preserve the exact v1 dump shape when no call ID was supplied.
        kwargs.setdefault("exclude_none", True)
        return super().model_dump(*args, **kwargs)


class TokenUsage(BaseModel):
    """Provider-neutral token accounting attached to an LLM response."""

    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0


class LLMResponse(BaseModel):
    content: str
    tool_calls: Optional[List[ToolCall]] = None
    model: Optional[str] = None
    finish_reason: Optional[str] = None
    usage: Optional[TokenUsage] = None


class AgentMessage(BaseModel):
    role: str  # system, user, assistant, tool
    content: str
    tool_calls: Optional[List[ToolCall]] = None
    tool_call_id: Optional[str] = None


# ── Tool execution and evaluation ──


class ToolExecutionStatus(str, Enum):
    SUCCESS = "success"
    ERROR = "error"
    DENIED = "denied"
    BLOCKED = "blocked"


class ToolExecution(BaseModel):
    """The complete request/result record for one tool invocation."""

    call_id: str
    name: str
    arguments: Dict[str, Any] = Field(default_factory=dict)
    status: ToolExecutionStatus = ToolExecutionStatus.SUCCESS
    output: Optional[str] = None
    error: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class StopSignal(BaseModel):
    """Mid-loop evaluation: should the engine stop early?"""

    should_stop: bool = False
    reason: str = ""
    stop_category: str = ""  # error_loop | spinning | stagnation | custom | ""


class EvaluationResult(BaseModel):
    """Post-loop evaluation: did the agent achieve the task?"""

    achieved: bool = False
    confidence: float = 0.0
    explanation: str = ""
    evidence: List[str] = Field(default_factory=list)


class StepResult(BaseModel):
    """One model turn and all tool executions it produced.

    ``actions`` is the v2 lossless trace. ``action`` and ``observation`` are
    retained as a v1 compatibility view and point at the final tool execution
    in the step, matching the old engine's behavior for multi-tool responses.
    """

    step_id: int
    thought: str
    actions: List[ToolExecution] = Field(default_factory=list)
    action: Optional[Dict[str, Any]] = None
    observation: Optional[str] = None
    status: str = "success"  # success, error, terminated
    stop_signal: Optional[StopSignal] = None


# ── Run lifecycle ──


class RunStatus(str, Enum):
    RUNNING = "running"
    COMPLETED = "completed"
    STOPPED = "stopped"
    MAX_STEPS = "max_steps"
    FAILED = "failed"


class RunContext(BaseModel):
    """Identity and immutable inputs for one ``NanoEngine.run`` call."""

    run_id: str = Field(default_factory=lambda: _new_id("run"))
    session_id: str = Field(default_factory=lambda: _new_id("session"))
    query: str
    max_steps: int
    protocol_version: str = CORE_PROTOCOL_VERSION
    started_at: datetime = Field(default_factory=_utc_now)


class RunCheckpoint(BaseModel):
    """Crash-readable snapshot written through ``BaseStateStore``."""

    run_id: str
    session_id: str
    query: str
    current_step: int = -1
    status: RunStatus = RunStatus.RUNNING
    trajectory: List[StepResult] = Field(default_factory=list)
    stop_reason: str = ""
    error: str = ""
    protocol_version: str = CORE_PROTOCOL_VERSION
    updated_at: datetime = Field(default_factory=_utc_now)


class EventType(str, Enum):
    RUN_STARTED = "run_started"
    RUN_COMPLETED = "run_completed"
    RUN_FAILED = "run_failed"
    STEP_STARTED = "step_started"
    STEP_COMPLETED = "step_completed"
    MODEL_REQUESTED = "model_requested"
    MODEL_RESPONDED = "model_responded"
    TOOL_REQUESTED = "tool_requested"
    TOOL_COMPLETED = "tool_completed"
    TOOL_FAILED = "tool_failed"
    TOOL_DENIED = "tool_denied"
    TOOL_BLOCKED = "tool_blocked"
    EVALUATION_COMPLETED = "evaluation_completed"


class HarnessEvent(BaseModel):
    """An ordered, provider-neutral fact emitted during one run."""

    event_id: str = Field(default_factory=lambda: _new_id("event"))
    run_id: str
    session_id: str
    sequence: int
    type: EventType
    timestamp: datetime = Field(default_factory=_utc_now)
    step_id: Optional[int] = None
    data: Dict[str, Any] = Field(default_factory=dict)
