"""Host-owned interaction and staged-learning tools for NanoHermes."""

import hashlib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Dict, Iterable

from nanoharness.extensions import ExtensionContext

from app.models import (
    HermesPhase,
    HermesRunState,
    HermesStatus,
    HermesTransition,
    LEARNING_NAME,
    LearningProposal,
    ProposalKind,
    content_sha256,
    safe_relative_path,
)
from app.store import HermesRunStore


_INTERNAL_DIRECTORIES = {".git", ".nano_hermes"}
_MAX_TOOL_OUTPUT = 20_000


def _function_schema(
    name: str,
    description: str,
    properties: Dict[str, Any],
    required: Iterable[str],
) -> Dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": list(required),
                "additionalProperties": False,
            },
        },
    }


@dataclass
class HermesToolRuntime:
    state: HermesRunState
    store: HermesRunStore
    context: ExtensionContext
    workspace: Path
    memory_root: Path
    skills_root: Path
    staging_root: Path

    def transition(self, target: HermesPhase, reason: str) -> None:
        source = self.state.phase
        if source == target:
            return
        self.state.transitions.append(
            HermesTransition(source=source, target=target, reason=reason)
        )
        self.state.phase = target
        self.state.status = (
            HermesStatus.COMPLETED
            if target == HermesPhase.COMPLETED
            else HermesStatus.PENDING
        )
        self.state.error = ""
        self.store.save(self.state)

    def resolve_workspace_path(self, raw_path: str) -> tuple[str, Path]:
        relative = safe_relative_path(raw_path)
        parts = PurePosixPath(relative).parts
        if parts and parts[0].lower() in _INTERNAL_DIRECTORIES:
            raise ValueError(f"agent tools cannot access internal path: {raw_path!r}")
        root = self.workspace.resolve()
        target = (root / PurePosixPath(relative)).resolve()
        if target != root and root not in target.parents:
            raise ValueError(f"path escapes persistent workspace: {raw_path!r}")
        resolved_parts = target.relative_to(root).parts
        if resolved_parts and resolved_parts[0].lower() in _INTERNAL_DIRECTORIES:
            raise ValueError(f"agent tools cannot access internal path: {raw_path!r}")
        return relative, target


def register_hermes_tools(
    context: ExtensionContext,
    runtime: HermesToolRuntime,
) -> None:
    def workspace_read(args: Dict[str, Any]) -> str:
        raw_path = str(args.get("path") or "").strip()
        if not raw_path:
            raise RuntimeError("path is required")
        _, target = runtime.resolve_workspace_path(raw_path)
        return _truncate(target.read_text(encoding="utf-8"))

    def workspace_write(args: Dict[str, Any]) -> str:
        _require_phase(runtime.state, HermesPhase.ASSIST)
        raw_path = str(args.get("path") or "").strip()
        if not raw_path:
            raise RuntimeError("path is required")
        if "content" not in args:
            raise RuntimeError("content is required")
        relative, target = runtime.resolve_workspace_path(raw_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(str(args["content"]), encoding="utf-8")
        return f"wrote {relative}"

    def assist_submit(args: Dict[str, Any]) -> str:
        _require_phase(runtime.state, HermesPhase.ASSIST)
        answer = str(args.get("answer") or "").strip()
        if not answer:
            raise RuntimeError("answer is required")
        if runtime.state.response and runtime.state.response != answer:
            raise RuntimeError("a different answer is already persisted for this run")
        runtime.state.response = answer
        runtime.store.save(runtime.state)
        runtime.transition(HermesPhase.REFLECT, "assistant response submitted")
        return "Response persisted; begin a separate reflection pass"

    def memory_propose(args: Dict[str, Any]) -> str:
        _require_phase(runtime.state, HermesPhase.REFLECT)
        proposal = _new_proposal(
            runtime,
            ProposalKind.MEMORY,
            name=str(args.get("name") or "").strip(),
            content=str(args.get("content") or ""),
            description=str(args.get("description") or ""),
            memory_type=str(args.get("type") or "note"),
        )
        return f"staged memory proposal {proposal.name} ({proposal.content_sha256[:12]})"

    def skill_propose(args: Dict[str, Any]) -> str:
        _require_phase(runtime.state, HermesPhase.REFLECT)
        proposal = _new_proposal(
            runtime,
            ProposalKind.SKILL,
            name=str(args.get("name") or "").strip(),
            content=str(args.get("content") or ""),
            description=str(args.get("description") or ""),
            trigger=str(args.get("trigger") or ""),
        )
        return f"staged skill proposal {proposal.name} ({proposal.content_sha256[:12]})"

    def reflection_submit(args: Dict[str, Any]) -> str:
        _require_phase(runtime.state, HermesPhase.REFLECT)
        summary = str(args.get("summary") or "").strip()
        if not summary:
            raise RuntimeError("summary is required")
        runtime.state.reflection_summary = summary
        runtime.store.save(runtime.state)
        runtime.transition(HermesPhase.REVIEW, "reflection submitted for host review")
        return f"Reflection submitted with {len(runtime.state.proposals)} proposal(s)"

    definitions = [
        (
            "workspace_read",
            workspace_read,
            "Read one bounded UTF-8 file from the persistent workspace.",
            {"path": {"type": "string"}},
            ["path"],
        ),
        (
            "workspace_write",
            workspace_write,
            "Write one UTF-8 file in the persistent workspace after approval.",
            {
                "path": {"type": "string"},
                "content": {"type": "string"},
            },
            ["path", "content"],
        ),
        (
            "assist_submit",
            assist_submit,
            "Submit the user-facing response and begin reflection.",
            {"answer": {"type": "string"}},
            ["answer"],
        ),
        (
            "memory_propose",
            memory_propose,
            "Stage a durable memory proposal for host validation and review.",
            {
                "name": {"type": "string"},
                "content": {"type": "string"},
                "description": {"type": "string"},
                "type": {
                    "type": "string",
                    "enum": ["note", "feedback", "reference", "project"],
                },
            },
            ["name", "content"],
        ),
        (
            "skill_propose",
            skill_propose,
            "Stage a procedural skill proposal without modifying active skills.",
            {
                "name": {"type": "string"},
                "content": {"type": "string"},
                "description": {"type": "string"},
                "trigger": {"type": "string"},
            },
            ["name", "content"],
        ),
        (
            "reflection_submit",
            reflection_submit,
            "Finish reflection and hand staged proposals to the host reviewer.",
            {"summary": {"type": "string"}},
            ["summary"],
        ),
    ]
    for name, handler, description, properties, required in definitions:
        context.register_tool(
            name,
            handler,
            _function_schema(name, description, properties, required),
        )


def _new_proposal(
    runtime: HermesToolRuntime,
    kind: ProposalKind,
    *,
    name: str,
    content: str,
    description: str = "",
    memory_type: str = "note",
    trigger: str = "",
) -> LearningProposal:
    if not LEARNING_NAME.fullmatch(name):
        raise ValueError("learning name contains unsupported characters")
    existing = [
        proposal
        for proposal in runtime.state.proposals
        if proposal.kind == kind and proposal.name == name
    ]
    if existing:
        proposal = existing[0]
        if (
            proposal.content != content
            or proposal.description != description
            or proposal.memory_type != memory_type
            or proposal.trigger != trigger
        ):
            raise RuntimeError(
                f"a different {kind.value} proposal already exists for {name!r}"
            )
        return proposal
    identity = f"{runtime.state.run_id}:{kind.value}:{name}".encode("utf-8")
    proposal_id = "proposal_" + hashlib.sha256(identity).hexdigest()[:24]
    target_root = (
        runtime.memory_root if kind == ProposalKind.MEMORY else runtime.skills_root
    )
    target = target_root / f"{name}.md"
    base_sha256 = (
        content_sha256(target.read_text(encoding="utf-8"))
        if target.is_file()
        else None
    )
    staged_path = runtime.staging_root / f"{proposal_id}.json"
    proposal = LearningProposal(
        proposal_id=proposal_id,
        kind=kind,
        name=name,
        content=content,
        description=description,
        memory_type=memory_type,
        trigger=trigger,
        source_run_id=runtime.state.run_id,
        base_sha256=base_sha256,
        staged_path=str(staged_path.resolve()),
    )
    runtime.store.stage_proposal(proposal, runtime.staging_root)
    runtime.state.proposals.append(proposal)
    runtime.store.save(runtime.state)
    return proposal


def _require_phase(state: HermesRunState, expected: HermesPhase) -> None:
    if state.phase != expected:
        raise RuntimeError(
            f"tool requires {expected.value} phase; current phase is {state.phase.value}"
        )


def _truncate(value: str) -> str:
    if len(value) <= _MAX_TOOL_OUTPUT:
        return value
    return value[:_MAX_TOOL_OUTPUT] + "\n... output truncated ..."
