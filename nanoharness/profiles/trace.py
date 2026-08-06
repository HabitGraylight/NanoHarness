"""Content-minimizing trace summaries and comparisons for NanoEngine runs."""

import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


class TraceStep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    step_id: int
    status: str
    thought_chars: int = 0
    observation_chars: int = 0
    tools: List[str] = Field(default_factory=list)
    tool_statuses: List[str] = Field(default_factory=list)
    stop_reason: str = ""


class HarnessTrace(BaseModel):
    """Normalized metrics without raw thoughts, arguments, or tool output."""

    model_config = ConfigDict(extra="forbid")

    source_kind: str
    run_id: str = ""
    session_id: str = ""
    protocol_version: str = ""
    status: str = "unknown"
    stop_reason: str = ""
    success: Optional[bool] = None
    achieved: Optional[bool] = None
    confidence: Optional[float] = None
    total_steps: int = 0
    total_tool_calls: int = 0
    duration_seconds: Optional[float] = None
    tool_counts: Dict[str, int] = Field(default_factory=dict)
    tool_status_counts: Dict[str, int] = Field(default_factory=dict)
    step_status_counts: Dict[str, int] = Field(default_factory=dict)
    event_counts: Dict[str, int] = Field(default_factory=dict)
    policy_outcomes: Dict[str, int] = Field(default_factory=dict)
    steps: List[TraceStep] = Field(default_factory=list)


class TraceMetricComparison(BaseModel):
    model_config = ConfigDict(extra="forbid")

    metric: str
    left: Any = None
    right: Any = None
    delta: Optional[float] = None


class TraceComparison(BaseModel):
    model_config = ConfigDict(extra="forbid")

    left: str
    right: str
    delta_semantics: str = "right - left"
    metrics: List[TraceMetricComparison] = Field(default_factory=list)
    tool_call_deltas: Dict[str, int] = Field(default_factory=dict)
    tool_status_deltas: Dict[str, int] = Field(default_factory=dict)
    step_status_deltas: Dict[str, int] = Field(default_factory=dict)
    event_deltas: Dict[str, int] = Field(default_factory=dict)
    policy_outcome_deltas: Dict[str, int] = Field(default_factory=dict)


def load_trace(path: str) -> HarnessTrace:
    source = Path(path)
    text = source.read_text(encoding="utf-8")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        payload = [
            json.loads(line)
            for line in text.splitlines()
            if line.strip()
        ]
    return summarize_trace(payload)


def summarize_trace(payload: Any) -> HarnessTrace:
    if isinstance(payload, list):
        return _from_events(payload)
    if not isinstance(payload, dict):
        raise ValueError("Trace input must be a report/checkpoint object or event list")
    if "events" in payload or "summary" in payload or "run" in payload:
        return _from_report(payload)
    if "trajectory" in payload and "status" in payload:
        return _from_checkpoint(payload)
    if _looks_like_event(payload):
        return _from_events([payload])
    raise ValueError("Unrecognized NanoHarness trace shape")


def compare_traces(
    left: HarnessTrace,
    right: HarnessTrace,
    *,
    left_label: str = "left",
    right_label: str = "right",
) -> TraceComparison:
    metrics = []
    for name in (
        "status",
        "stop_reason",
        "success",
        "achieved",
        "confidence",
        "total_steps",
        "total_tool_calls",
        "duration_seconds",
    ):
        left_value = getattr(left, name)
        right_value = getattr(right, name)
        delta = None
        if (
            isinstance(left_value, (int, float))
            and not isinstance(left_value, bool)
            and isinstance(right_value, (int, float))
            and not isinstance(right_value, bool)
        ):
            delta = round(float(right_value - left_value), 12)
        metrics.append(TraceMetricComparison(
            metric=name,
            left=left_value,
            right=right_value,
            delta=delta,
        ))
    return TraceComparison(
        left=left_label,
        right=right_label,
        metrics=metrics,
        tool_call_deltas=_counter_delta(left.tool_counts, right.tool_counts),
        tool_status_deltas=_counter_delta(
            left.tool_status_counts,
            right.tool_status_counts,
        ),
        step_status_deltas=_counter_delta(
            left.step_status_counts,
            right.step_status_counts,
        ),
        event_deltas=_counter_delta(left.event_counts, right.event_counts),
        policy_outcome_deltas=_counter_delta(
            left.policy_outcomes,
            right.policy_outcomes,
        ),
    )


def _from_report(report: Dict[str, Any]) -> HarnessTrace:
    run = report.get("run") or {}
    summary = report.get("summary") or {}
    evaluation = summary.get("evaluation") or {}
    events = report.get("events") or []
    steps, tools, tool_statuses, step_statuses = _summarize_steps(
        report.get("trajectory") or []
    )
    return HarnessTrace(
        source_kind="report",
        run_id=str(run.get("run_id") or summary.get("run_id") or ""),
        session_id=str(run.get("session_id") or ""),
        protocol_version=str(run.get("protocol_version") or ""),
        status=str(run.get("status") or summary.get("run_status") or "unknown"),
        stop_reason=str(run.get("stop_reason") or summary.get("stop_reason") or ""),
        success=_optional_bool(summary.get("success")),
        achieved=_optional_bool(evaluation.get("achieved")),
        confidence=_optional_float(evaluation.get("confidence")),
        total_steps=len(steps),
        total_tool_calls=sum(tools.values()),
        duration_seconds=_duration(events),
        tool_counts=dict(sorted(tools.items())),
        tool_status_counts=dict(sorted(tool_statuses.items())),
        step_status_counts=dict(sorted(step_statuses.items())),
        event_counts=_event_counts(events),
        policy_outcomes=_policy_counts(events),
        steps=steps,
    )


def _from_checkpoint(checkpoint: Dict[str, Any]) -> HarnessTrace:
    steps, tools, tool_statuses, step_statuses = _summarize_steps(
        checkpoint.get("trajectory") or []
    )
    return HarnessTrace(
        source_kind="checkpoint",
        run_id=str(checkpoint.get("run_id") or ""),
        session_id=str(checkpoint.get("session_id") or ""),
        protocol_version=str(checkpoint.get("protocol_version") or ""),
        status=str(checkpoint.get("status") or "unknown"),
        stop_reason=str(checkpoint.get("stop_reason") or ""),
        total_steps=len(steps),
        total_tool_calls=sum(tools.values()),
        tool_counts=dict(sorted(tools.items())),
        tool_status_counts=dict(sorted(tool_statuses.items())),
        step_status_counts=dict(sorted(step_statuses.items())),
        steps=steps,
    )


def _from_events(events: List[Dict[str, Any]]) -> HarnessTrace:
    normalized = [event for event in events if isinstance(event, dict)]
    if not normalized:
        return HarnessTrace(source_kind="events")
    first = normalized[0]
    final_data = normalized[-1].get("data") or {}
    tools = Counter()
    statuses = Counter()
    step_ids = set()
    evaluation = {}
    for event in normalized:
        if event.get("step_id") is not None:
            step_ids.add(int(event["step_id"]))
        execution = (event.get("data") or {}).get("execution") or {}
        if event.get("type") in {
            "tool_completed", "tool_failed", "tool_denied", "tool_blocked"
        } and execution.get("name"):
            tools[str(execution["name"])] += 1
            statuses[str(execution.get("status") or "unknown")] += 1
        if event.get("type") == "evaluation_completed":
            evaluation = (event.get("data") or {}).get("evaluation") or {}
    status = str(final_data.get("status") or _status_from_event(normalized[-1]))
    return HarnessTrace(
        source_kind="events",
        run_id=str(first.get("run_id") or ""),
        session_id=str(first.get("session_id") or ""),
        status=status,
        stop_reason=str(final_data.get("stop_reason") or ""),
        success=_optional_bool(final_data.get("success")),
        achieved=_optional_bool(evaluation.get("achieved")),
        confidence=_optional_float(evaluation.get("confidence")),
        total_steps=len(step_ids),
        total_tool_calls=sum(tools.values()),
        duration_seconds=_duration(normalized),
        tool_counts=dict(sorted(tools.items())),
        tool_status_counts=dict(sorted(statuses.items())),
        event_counts=_event_counts(normalized),
        policy_outcomes=_policy_counts(normalized),
    )


def _summarize_steps(trajectory):
    steps = []
    tools = Counter()
    tool_statuses = Counter()
    step_statuses = Counter()
    for index, raw in enumerate(trajectory):
        if not isinstance(raw, dict):
            continue
        actions = raw.get("actions") or []
        names = []
        statuses = []
        for action in actions:
            if not isinstance(action, dict):
                continue
            name = str(action.get("name") or "unknown")
            status = str(action.get("status") or "unknown")
            names.append(name)
            statuses.append(status)
            tools[name] += 1
            tool_statuses[status] += 1
        status = str(raw.get("status") or "unknown")
        step_statuses[status] += 1
        stop_signal = raw.get("stop_signal") or {}
        steps.append(TraceStep(
            step_id=int(raw.get("step_id", index)),
            status=status,
            thought_chars=len(str(raw.get("thought") or "")),
            observation_chars=len(str(raw.get("observation") or "")),
            tools=names,
            tool_statuses=statuses,
            stop_reason=str(stop_signal.get("reason") or ""),
        ))
    return steps, tools, tool_statuses, step_statuses


def _event_counts(events) -> Dict[str, int]:
    counts = Counter(
        str(event.get("type") or "unknown")
        for event in events
        if isinstance(event, dict)
    )
    return dict(sorted(counts.items()))


def _policy_counts(events) -> Dict[str, int]:
    counts = Counter()
    for event in events:
        if not isinstance(event, dict) or event.get("type") != "policy_evaluated":
            continue
        decision = (event.get("data") or {}).get("decision") or {}
        counts[str(decision.get("outcome") or "unknown")] += 1
    return dict(sorted(counts.items()))


def _duration(events) -> Optional[float]:
    timestamps = []
    for event in events:
        if not isinstance(event, dict) or not event.get("timestamp"):
            continue
        try:
            timestamps.append(datetime.fromisoformat(
                str(event["timestamp"]).replace("Z", "+00:00")
            ))
        except ValueError:
            continue
    if len(timestamps) < 2:
        return None
    try:
        return max(0.0, (timestamps[-1] - timestamps[0]).total_seconds())
    except TypeError:
        return None


def _counter_delta(left: Dict[str, int], right: Dict[str, int]) -> Dict[str, int]:
    return {
        key: right.get(key, 0) - left.get(key, 0)
        for key in sorted(set(left) | set(right))
        if right.get(key, 0) - left.get(key, 0) != 0
    }


def _optional_bool(value) -> Optional[bool]:
    return value if isinstance(value, bool) else None


def _optional_float(value) -> Optional[float]:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return None


def _looks_like_event(value: Dict[str, Any]) -> bool:
    return "type" in value and "sequence" in value and "run_id" in value


def _status_from_event(event: Dict[str, Any]) -> str:
    return {
        "run_completed": "completed",
        "run_cancelled": "cancelled",
        "run_failed": "failed",
    }.get(str(event.get("type") or ""), "unknown")
