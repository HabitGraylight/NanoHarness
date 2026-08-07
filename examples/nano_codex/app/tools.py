"""Phase-aware tools owned by the NanoCodex host."""

from dataclasses import dataclass
from pathlib import Path, PurePosixPath
import subprocess
from typing import Any, Dict, Iterable

from nanoharness.extensions import ExtensionContext

from app.models import (
    CodexPhase,
    CodexJob,
    CodexRunState,
    CodexStatus,
    DeliveryStatus,
    PhaseTransition,
    _safe_relative_path,
)
from app.store import CodexRunStore


_INTERNAL_DIRECTORIES = {".git", ".nano_codex"}
_MAX_TOOL_OUTPUT = 20_000
_MAX_SEARCH_FILE_BYTES = 1_000_000
_MAX_SEARCH_RESULTS = 100


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
class CodexToolRuntime:
    job: CodexJob
    state: CodexRunState
    store: CodexRunStore
    context: ExtensionContext
    repository: Path

    @property
    def workspace(self) -> Path:
        configured = self.state.active_workspace
        return Path(configured).resolve() if configured else self.repository.resolve()

    def transition(self, target: CodexPhase, reason: str) -> None:
        source = self.state.phase
        if source == target:
            return
        self.state.transitions.append(
            PhaseTransition(source=source, target=target, reason=reason)
        )
        self.state.phase = target
        self.state.status = (
            CodexStatus.COMPLETED
            if target == CodexPhase.COMPLETED
            else CodexStatus.PENDING
        )
        self.state.error = ""
        self.store.save(self.state)

    def resolve_agent_path(
        self,
        raw_path: str,
        *,
        allow_root: bool = False,
    ) -> tuple[str, Path]:
        if allow_root and raw_path.strip().replace("\\", "/") in {"", ".", "./"}:
            return ".", self.workspace.resolve()
        relative = _safe_relative_path(raw_path)
        parts = PurePosixPath(relative).parts
        if parts and parts[0].lower() in _INTERNAL_DIRECTORIES:
            raise ValueError(f"agent tools cannot access internal path: {raw_path!r}")
        root = self.workspace.resolve()
        target = (root / PurePosixPath(relative)).resolve()
        if target != root and root not in target.parents:
            raise ValueError(f"path escapes active workspace: {raw_path!r}")
        resolved_parts = target.relative_to(root).parts
        if resolved_parts and resolved_parts[0].lower() in _INTERNAL_DIRECTORIES:
            raise ValueError(f"agent tools cannot access internal path: {raw_path!r}")
        return relative, target


def register_codex_tools(context: ExtensionContext, runtime: CodexToolRuntime) -> None:
    """Register host-owned tools with explicit array schemas."""

    def plan_submit(args: Dict[str, Any]) -> str:
        _require_phase(runtime.state, CodexPhase.PLAN)
        raw_steps = args.get("steps")
        if not isinstance(raw_steps, list) or not raw_steps:
            raise RuntimeError("steps must be a non-empty array")
        steps = [str(step).strip() for step in raw_steps]
        if any(not step for step in steps):
            raise RuntimeError("plan steps cannot be empty")
        if runtime.state.plan_steps and runtime.state.plan_steps != steps:
            raise RuntimeError("a different plan is already persisted for this run")
        if not runtime.state.plan_steps:
            runtime.state.plan_steps = steps
            runtime.store.save(runtime.state)

        board = runtime.context.services.get("tasks")
        worktrees = runtime.context.services.get("worktrees")
        if board is None or worktrees is None:
            raise RuntimeError("Task Board and Worktree services must be installed")

        if runtime.state.root_task_id is None:
            root_marker = f"nano-codex:{runtime.state.run_id}:root"
            root_task = _find_marked_task(board, root_marker)
            if root_task is None:
                root_task = board.add(
                    subject=runtime.state.objective,
                    description=f"NanoCodex root task [{root_marker}]",
                    owner="nano-codex",
                )
            runtime.state.root_task_id = int(root_task["id"])
            runtime.store.save(runtime.state)

        while len(runtime.state.step_task_ids) < len(steps):
            index = len(runtime.state.step_task_ids)
            dependencies = (
                [runtime.state.step_task_ids[-1]]
                if runtime.state.step_task_ids
                else []
            )
            marker = f"nano-codex:{runtime.state.run_id}:step:{index + 1}"
            task = _find_marked_task(board, marker)
            if task is None:
                task = board.add(
                    subject=steps[index],
                    description=(
                        f"Plan step {index + 1} of {len(steps)} [{marker}]"
                    ),
                    blocked_by=dependencies,
                    owner="nano-codex",
                )
            runtime.state.step_task_ids.append(int(task["id"]))
            runtime.store.save(runtime.state)

        worktree_name = runtime.state.worktree_name
        if worktree_name is None:
            worktree_name = f"run-{runtime.state.run_id[-12:]}"
            runtime.state.worktree_name = worktree_name
            runtime.store.save(runtime.state)
        record = worktrees.get(worktree_name)
        if record is None:
            record = worktrees.create(
                worktree_name,
                task_id=int(runtime.state.root_task_id),
            )
        active_workspace = (runtime.repository / record["path"]).resolve()
        runtime.state.active_workspace = str(active_workspace)
        runtime.store.save(runtime.state)
        runtime.transition(CodexPhase.EXECUTE, "plan submitted and worktree created")
        return f"Accepted {len(steps)} plan steps in worktree {worktree_name}"

    def workspace_read(args: Dict[str, Any]) -> str:
        raw_path = str(args.get("path") or "").strip()
        if not raw_path:
            raise RuntimeError("path is required")
        _, target = runtime.resolve_agent_path(raw_path)
        return _truncate(target.read_text(encoding="utf-8"))

    def workspace_list(args: Dict[str, Any]) -> str:
        raw_path = str(args.get("path") or ".").strip()
        recursive = bool(args.get("recursive", False))
        _, target = runtime.resolve_agent_path(raw_path, allow_root=True)
        if not target.is_dir():
            raise RuntimeError(f"not a directory: {raw_path}")
        entries = target.rglob("*") if recursive else target.iterdir()
        lines = []
        root = runtime.workspace.resolve()
        for entry in sorted(entries):
            relative = entry.relative_to(root)
            if relative.parts and relative.parts[0].lower() in _INTERNAL_DIRECTORIES:
                continue
            suffix = "/" if entry.is_dir() else ""
            lines.append(relative.as_posix() + suffix)
            if len(lines) >= _MAX_SEARCH_RESULTS:
                lines.append("... output truncated ...")
                break
        return "\n".join(lines) if lines else "Workspace directory is empty."

    def workspace_search(args: Dict[str, Any]) -> str:
        query = str(args.get("query") or "")
        if not query:
            raise RuntimeError("query is required")
        raw_path = str(args.get("path") or ".").strip()
        glob = str(args.get("glob") or "*").strip() or "*"
        _, target = runtime.resolve_agent_path(raw_path, allow_root=True)
        if not target.is_dir():
            raise RuntimeError(f"not a directory: {raw_path}")
        root = runtime.workspace.resolve()
        matches = []
        for candidate in sorted(target.rglob("*")):
            if not candidate.is_file():
                continue
            relative = candidate.relative_to(root)
            if relative.parts and relative.parts[0].lower() in _INTERNAL_DIRECTORIES:
                continue
            try:
                _, safe_candidate = runtime.resolve_agent_path(relative.as_posix())
            except ValueError:
                continue
            if not candidate.match(glob) and not relative.match(glob):
                continue
            try:
                if safe_candidate.stat().st_size > _MAX_SEARCH_FILE_BYTES:
                    continue
                content = safe_candidate.read_text(encoding="utf-8")
            except (OSError, UnicodeError):
                continue
            for line_number, line in enumerate(content.splitlines(), start=1):
                if query in line:
                    matches.append(f"{relative.as_posix()}:{line_number}:{line}")
                    if len(matches) >= _MAX_SEARCH_RESULTS:
                        matches.append("... results truncated ...")
                        return _truncate("\n".join(matches))
        return _truncate("\n".join(matches)) if matches else "No matches found."

    def workspace_write(args: Dict[str, Any]) -> str:
        _require_phase(runtime.state, CodexPhase.EXECUTE)
        raw_path = str(args.get("path") or "").strip()
        if not raw_path:
            raise RuntimeError("path is required")
        if "content" not in args:
            raise RuntimeError("content is required")
        relative, target = runtime.resolve_agent_path(raw_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(str(args["content"]), encoding="utf-8")
        if relative not in runtime.state.changed_files:
            runtime.state.changed_files.append(relative)
        runtime.store.save(runtime.state)
        return f"wrote {relative}"

    def workspace_patch(args: Dict[str, Any]) -> str:
        _require_phase(runtime.state, CodexPhase.EXECUTE)
        raw_path = str(args.get("path") or "").strip()
        old_text = str(args.get("old_text") or "")
        new_text = str(args.get("new_text") or "")
        expected = int(args.get("expected_replacements", 1))
        if not raw_path:
            raise RuntimeError("path is required")
        if not old_text:
            raise RuntimeError("old_text cannot be empty")
        if expected < 1:
            raise RuntimeError("expected_replacements must be positive")
        relative, target = runtime.resolve_agent_path(raw_path)
        content = target.read_text(encoding="utf-8")
        actual = content.count(old_text)
        if actual != expected:
            raise RuntimeError(
                f"expected {expected} replacement(s), found {actual} in {relative}"
            )
        target.write_text(content.replace(old_text, new_text), encoding="utf-8")
        if relative not in runtime.state.changed_files:
            runtime.state.changed_files.append(relative)
        runtime.store.save(runtime.state)
        return f"patched {relative} ({actual} replacement(s))"

    def workspace_status(args: Dict[str, Any]) -> str:
        return _run_argv(
            ["git", "status", "--short"],
            runtime.workspace,
            timeout=30,
            empty="Working tree is clean.",
        )

    def workspace_diff(args: Dict[str, Any]) -> str:
        return _run_argv(
            ["git", "diff", "--no-ext-diff", "--", "."],
            runtime.workspace,
            timeout=30,
            empty="No tracked diff.",
        )

    def workspace_test(args: Dict[str, Any]) -> str:
        name = str(args.get("name") or "").strip()
        if not name:
            raise RuntimeError("name is required")
        command = runtime.job.commands.get(name)
        if command is None:
            available = ", ".join(sorted(runtime.job.commands)) or "none"
            raise RuntimeError(f"unknown trusted command {name!r}; available: {available}")
        return _run_argv(
            command.argv,
            runtime.workspace,
            timeout=command.timeout_seconds,
            empty=f"Trusted command {name!r} passed with no output.",
        )

    def execution_finish(args: Dict[str, Any]) -> str:
        _require_phase(runtime.state, CodexPhase.EXECUTE)
        summary = str(args.get("summary") or "").strip()
        if not summary:
            raise RuntimeError("summary is required")
        if not runtime.state.changed_files:
            raise RuntimeError("execution cannot finish without a workspace change")
        status = _run_argv(
            ["git", "status", "--short"],
            runtime.workspace,
            timeout=30,
            empty="",
        )
        if not status:
            raise RuntimeError("execution cannot finish without an actual Git change")
        board = runtime.context.services.get("tasks")
        if board is None:
            raise RuntimeError("Task Board service must be installed")
        for task_id in runtime.state.step_task_ids:
            task = board.get(task_id)
            if task is None:
                raise RuntimeError(f"persisted task #{task_id} is missing")
            status = getattr(task.get("status"), "value", task.get("status"))
            if status != "completed":
                board.complete(task_id)
        runtime.state.execution_summary = summary
        runtime.store.save(runtime.state)
        runtime.transition(CodexPhase.REVIEW, "execution reported complete")
        return f"Execution complete with {len(runtime.state.changed_files)} changed file(s)"

    def review_submit(args: Dict[str, Any]) -> str:
        _require_phase(runtime.state, CodexPhase.REVIEW)
        verdict = str(args.get("verdict") or "").strip().lower()
        if verdict not in {"pass", "fail"}:
            raise RuntimeError("verdict must be 'pass' or 'fail'")
        raw_findings = args.get("findings", [])
        if not isinstance(raw_findings, list):
            raise RuntimeError("findings must be an array")
        runtime.state.agent_review = verdict
        runtime.state.review_findings = [str(item) for item in raw_findings]
        runtime.store.save(runtime.state)
        return "Agent review recorded; the host will now verify trusted evidence"

    def delivery_submit(args: Dict[str, Any]) -> str:
        _require_phase(runtime.state, CodexPhase.REVIEW)
        if runtime.state.agent_review is None:
            raise RuntimeError("review_submit must run before delivery_submit")
        raw_mode = str(args.get("mode") or "").strip().lower()
        allowed = {mode.value: mode for mode in runtime.job.allowed_deliveries}
        if raw_mode not in allowed:
            raise RuntimeError(
                f"delivery mode {raw_mode!r} is not allowed; "
                f"choose from {sorted(allowed)}"
            )
        if raw_mode in {"apply", "merge"} and not runtime.state.source_repository:
            raise RuntimeError(f"delivery mode {raw_mode!r} requires a source repository")
        runtime.state.delivery_mode = allowed[raw_mode]
        runtime.state.delivery_status = DeliveryStatus.PENDING
        runtime.state.delivery_error = ""
        runtime.store.save(runtime.state)
        return f"Delivery {raw_mode!r} approved and queued after trusted evidence"

    definitions = [
        (
            "plan_submit",
            plan_submit,
            "Persist a plan and create its task-bound Git worktree.",
            {"steps": {"type": "array", "items": {"type": "string"}, "minItems": 1}},
            ["steps"],
        ),
        (
            "workspace_read",
            workspace_read,
            "Read one UTF-8 file from the active controlled workspace.",
            {"path": {"type": "string"}},
            ["path"],
        ),
        (
            "workspace_list",
            workspace_list,
            "List files inside the active workspace.",
            {
                "path": {"type": "string"},
                "recursive": {"type": "boolean"},
            },
            [],
        ),
        (
            "workspace_search",
            workspace_search,
            "Search literal text in bounded UTF-8 workspace files.",
            {
                "query": {"type": "string"},
                "path": {"type": "string"},
                "glob": {"type": "string"},
            },
            ["query"],
        ),
        (
            "workspace_write",
            workspace_write,
            "Write one UTF-8 file in the active worktree.",
            {"path": {"type": "string"}, "content": {"type": "string"}},
            ["path", "content"],
        ),
        (
            "workspace_patch",
            workspace_patch,
            "Replace an exact number of text occurrences in one workspace file.",
            {
                "path": {"type": "string"},
                "old_text": {"type": "string"},
                "new_text": {"type": "string"},
                "expected_replacements": {"type": "integer", "minimum": 1},
            },
            ["path", "old_text", "new_text"],
        ),
        (
            "workspace_status",
            workspace_status,
            "Show concise Git status for the active worktree.",
            {},
            [],
        ),
        (
            "workspace_diff",
            workspace_diff,
            "Show the tracked Git diff for the active worktree.",
            {},
            [],
        ),
        (
            "workspace_test",
            workspace_test,
            "Run a host-configured trusted command by name without a shell.",
            {"name": {"type": "string"}},
            ["name"],
        ),
        (
            "execution_finish",
            execution_finish,
            "Finish execution after at least one controlled workspace change.",
            {"summary": {"type": "string"}},
            ["summary"],
        ),
        (
            "review_submit",
            review_submit,
            "Submit an advisory review before trusted host evidence runs.",
            {
                "verdict": {"type": "string", "enum": ["pass", "fail"]},
                "findings": {"type": "array", "items": {"type": "string"}},
            },
            ["verdict", "findings"],
        ),
        (
            "delivery_submit",
            delivery_submit,
            "Request an allowed keep, commit, apply, or merge delivery.",
            {
                "mode": {
                    "type": "string",
                    "enum": ["keep", "commit", "apply", "merge"],
                }
            },
            ["mode"],
        ),
    ]
    for name, handler, description, properties, required in definitions:
        context.register_tool(
            name,
            handler,
            _function_schema(name, description, properties, required),
        )


def _require_phase(state: CodexRunState, expected: CodexPhase) -> None:
    if state.phase != expected:
        raise RuntimeError(
            f"tool requires {expected.value} phase; current phase is {state.phase.value}"
        )


def _find_marked_task(board, marker: str):
    matches = [
        task for task in board.list(owner="nano-codex")
        if f"[{marker}]" in str(task.get("description") or "")
    ]
    if len(matches) > 1:
        raise RuntimeError(f"multiple persisted tasks use marker {marker!r}")
    return matches[0] if matches else None


def _run_argv(
    argv: list[str],
    cwd: Path,
    *,
    timeout: int,
    empty: str,
) -> str:
    try:
        completed = subprocess.run(
            argv,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            shell=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise RuntimeError(f"command failed to start: {type(error).__name__}") from error
    output = (completed.stdout or "") + (completed.stderr or "")
    output = output.strip()
    if completed.returncode != 0:
        raise RuntimeError(
            _truncate(output) or f"command exited with code {completed.returncode}"
        )
    return _truncate(output) if output else empty


def _truncate(value: str) -> str:
    if len(value) <= _MAX_TOOL_OUTPUT:
        return value
    return value[:_MAX_TOOL_OUTPUT] + "\n... output truncated ..."
