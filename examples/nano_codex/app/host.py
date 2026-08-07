"""Complete, resumable Plan -> Execute -> Review NanoCodex host."""

import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any, List, Optional

from nanoharness.components import (
    DictToolRegistry,
    JsonStateStore,
    SimpleContextManager,
    SimpleHookManager,
    TraceEvaluator,
)
from nanoharness.components.lifecycle import JsonlEventSink, RegistryToolExecutor
from nanoharness.extensions import ExtensionContext
from nanoharness.profiles import HarnessBuilder, HarnessSpec
from nanoharness.testing import (
    RunArtifactStore,
    ScriptedLLM,
    ScriptedResponse,
    bind_profile_paths,
)

from app.approvals import ApprovalDecider, RecordingApprovalBroker
from app.models import (
    CodexJob,
    CodexPhase,
    CodexRunResult,
    CodexRunState,
    CodexStatus,
    DeliveryMode,
    DeliveryStatus,
    PhaseTransition,
)
from app.policy import CodexPolicy
from app.reviewer import EvidenceRunner
from app.store import CodexRunStore
from app.tools import CodexToolRuntime, register_codex_tools


ProviderFactory = Callable[[CodexPhase, List[ScriptedResponse]], Any]


class CodexHost:
    """App-owned orchestration around reusable NanoHarness components."""

    def __init__(
        self,
        job: CodexJob | str | Path,
        root: str | Path,
        *,
        profile_path: Optional[str | Path] = None,
        skills_root: Optional[str | Path] = None,
        repository: Optional[str | Path] = None,
        provider_factory: Optional[ProviderFactory] = None,
        approve_writes: ApprovalDecider = True,
        evidence_runner: Optional[EvidenceRunner] = None,
    ):
        self.job = job if isinstance(job, CodexJob) else CodexJob.from_file(job)
        self.root = Path(root).resolve()
        self.runtime_root = self.root / "runtime"
        self.repository = self.root / "workspace"
        self.source_repository = (
            Path(repository).resolve() if repository is not None else None
        )
        if self.source_repository is not None and (
            self.root == self.source_repository
            or self.root in self.source_repository.parents
            or self.source_repository in self.root.parents
        ):
            raise ValueError("output root and source repository must not overlap")
        self.artifact_root = self.root / "artifacts"
        example_root = Path(__file__).resolve().parents[1]
        self.profile_path = Path(profile_path or example_root / "profile.yaml").resolve()
        self.skills_root = Path(skills_root or example_root / "skills").resolve()
        if provider_factory is None:
            if not self.job.scripted:
                raise ValueError(
                    "a provider_factory is required when the job has no scripted phases"
                )
            provider_factory = lambda _phase, responses: ScriptedLLM(responses)
        self.provider_factory = provider_factory
        self.approve_writes = approve_writes
        self.evidence_runner = evidence_runner or EvidenceRunner()
        self.store = CodexRunStore(self.runtime_root / "run.json")
        self.artifacts = RunArtifactStore(self.artifact_root)

    def run(self) -> CodexRunResult:
        state = self._load_or_create_state()
        if state.phase == CodexPhase.COMPLETED:
            return self._result(state)

        # One invocation can advance through each forward-only phase once.
        for _ in range(3):
            started_phase = state.phase
            state.status = CodexStatus.RUNNING
            state.error = ""
            self.store.save(state)
            try:
                self._run_phase(state, started_phase)
            except Exception as error:
                state.status = CodexStatus.INTERRUPTED
                state.error = f"{type(error).__name__}: {error}"
                self.store.save(state)
                return self._result(state)

            if state.status in {
                CodexStatus.BLOCKED,
                CodexStatus.COMPLETED,
                CodexStatus.INTERRUPTED,
            }:
                return self._result(state)
            if state.phase == started_phase:
                state.status = CodexStatus.BLOCKED
                state.error = (
                    f"{started_phase.value} phase ended without its required "
                    "host transition tool"
                )
                self.store.save(state)
                return self._result(state)

        if state.phase != CodexPhase.COMPLETED:
            state.status = CodexStatus.INTERRUPTED
            state.error = "phase transition budget exhausted"
            self.store.save(state)
        return self._result(state)

    def _load_or_create_state(self) -> CodexRunState:
        self.runtime_root.mkdir(parents=True, exist_ok=True)
        if self.store.exists():
            state = self.store.load()
            if (
                state.job_name != self.job.name
                or state.job_fingerprint != self.job.fingerprint()
            ):
                raise ValueError(
                    "persisted NanoCodex run belongs to a different job; "
                    "choose another output directory"
                )
            if Path(state.repository).resolve() != self.repository.resolve():
                raise ValueError("persisted NanoCodex repository does not match this host")
            persisted_source = (
                Path(state.source_repository).resolve()
                if state.source_repository
                else None
            )
            if persisted_source != self.source_repository:
                raise ValueError("persisted NanoCodex source repository does not match")
            return state

        if self.repository.exists() and any(self.repository.iterdir()):
            raise ValueError(
                "NanoCodex workspace exists without runtime/run.json; "
                "choose another output directory"
            )
        if self.source_repository is not None:
            if self.job.fixture_files:
                raise ValueError(
                    "fixture_files are only supported for managed demo repositories"
                )
            source_head = self._prepare_source_repository()
            self._clone_source_repository()
        else:
            self.repository.mkdir(parents=True, exist_ok=True)
            self.job.materialize(self.repository)
            self._initialize_repository()
            source_head = None
        state = CodexRunState(
            job_name=self.job.name,
            job_fingerprint=self.job.fingerprint(),
            objective=self.job.objective,
            repository=str(self.repository.resolve()),
            source_repository=(
                str(self.source_repository) if self.source_repository else None
            ),
            source_head=source_head,
        )
        self.store.save(state)
        return state

    def _run_phase(self, state: CodexRunState, phase: CodexPhase) -> None:
        active_workspace = Path(state.active_workspace or self.repository).resolve()
        spec = HarnessSpec.from_file(str(self.profile_path))
        bound = bind_profile_paths(spec, {
            "workspace": str(self.repository.resolve()),
            "active_workspace": str(active_workspace),
            "runtime": str(self.runtime_root.resolve()),
            "skills": str(self.skills_root.resolve()),
        })
        tools = DictToolRegistry()
        services = self._services(state, phase, tools)
        context = ExtensionContext(
            tools=tools,
            services=services,
            capabilities=set(bound.host.capabilities),
            metadata={
                "workspace_root": str(self.repository.resolve()),
                "active_workspace": str(active_workspace),
                "phase": phase.value,
            },
        )
        register_codex_tools(
            context,
            CodexToolRuntime(
                self.job,
                state,
                self.store,
                context,
                self.repository,
            ),
        )
        build = HarnessBuilder().build(bound, context=context)
        try:
            if build.engine is None:
                raise RuntimeError("NanoCodex profile did not bind NanoEngine")
            engine_run_id = (
                f"{state.run_id}_{phase.value}_{len(state.artifacts) + 1}"
            )
            report = build.engine.run(
                self._phase_query(phase, state),
                run_id=engine_run_id,
                session_id=f"{state.run_id}_{phase.value}",
            )
            artifact, trace = self.artifacts.save(
                profile=bound.name,
                scenario=f"{self.job.name}-{phase.value}",
                report=report,
            )
            state.artifacts.append(artifact)
            state.total_steps += trace.total_steps
            for name, count in trace.tool_counts.items():
                state.tool_counts[name] = state.tool_counts.get(name, 0) + count
            self.store.save(state)
            if phase == CodexPhase.REVIEW:
                self._finalize_review(state, context)
        finally:
            build.close()

    def _services(
        self,
        state: CodexRunState,
        phase: CodexPhase,
        tools: DictToolRegistry,
    ) -> dict[str, Any]:
        binding = HarnessSpec.from_file(str(self.profile_path)).engine
        if binding is None:
            raise ValueError("NanoCodex profile must declare an engine")
        lifecycle_bindings = {
            "policy_service": binding.policy_service,
            "approval_broker_service": binding.approval_broker_service,
            "executor_service": binding.executor_service,
            "event_sink_service": binding.event_sink_service,
        }
        missing = [name for name, service in lifecycle_bindings.items() if not service]
        if missing:
            raise ValueError(
                "NanoCodex profile is missing controlled lifecycle bindings: "
                f"{missing}"
            )
        phase_runtime = self.runtime_root / "phases" / phase.value
        phase_runtime.mkdir(parents=True, exist_ok=True)
        provider = self.provider_factory(phase, self.job.phases.get(phase, []))
        return {
            binding.llm_service: provider,
            binding.context_service: SimpleContextManager(
                system_prompt=self._system_prompt(phase)
            ),
            binding.state_service: JsonStateStore(
                str(phase_runtime / "checkpoint.json")
            ),
            binding.hooks_service: SimpleHookManager(),
            binding.evaluator_service: TraceEvaluator(),
            binding.policy_service: CodexPolicy(state),
            binding.approval_broker_service: RecordingApprovalBroker(
                state,
                self.store,
                self.approve_writes,
            ),
            binding.executor_service: RegistryToolExecutor(tools),
            binding.event_sink_service: JsonlEventSink(
                str(phase_runtime / "events.jsonl")
            ),
        }

    def _finalize_review(
        self,
        state: CodexRunState,
        context: ExtensionContext,
    ) -> None:
        if state.agent_review is None:
            state.status = CodexStatus.BLOCKED
            state.error = "review phase ended without review_submit"
            self.store.save(state)
            return
        if not state.active_workspace:
            raise RuntimeError("review has no active worktree")
        if state.delivery_mode is None:
            state.status = CodexStatus.BLOCKED
            state.error = "review phase ended without delivery_submit"
            self.store.save(state)
            return
        state.evidence = self.evidence_runner.run(
            self.job.evidence,
            state.active_workspace,
        )
        if not state.evidence or not all(record.passed for record in state.evidence):
            state.status = CodexStatus.BLOCKED
            state.error = "trusted evidence did not pass"
            self.store.save(state)
            return

        self._deliver(state)

        worktrees = context.services.get("worktrees")
        if worktrees is None or state.worktree_name is None:
            raise RuntimeError("review cannot close out the active worktree")
        record = worktrees.get(state.worktree_name)
        if record is None:
            raise RuntimeError("persisted worktree is missing")
        if record.get("status") == "active":
            worktrees.closeout(
                state.worktree_name,
                action="keep",
                reason=(
                    "trusted NanoCodex evidence passed; "
                    f"delivery={state.delivery_mode.value}"
                ),
                complete_task=True,
            )
        state.transitions.append(PhaseTransition(
            source=CodexPhase.REVIEW,
            target=CodexPhase.COMPLETED,
            reason=f"trusted evidence passed; delivery={state.delivery_mode.value}",
        ))
        state.phase = CodexPhase.COMPLETED
        state.status = CodexStatus.COMPLETED
        state.error = ""
        self.store.save(state)

    def _initialize_repository(self) -> None:
        self._git("init")
        self._git("config", "user.name", "NanoCodex Demo")
        self._git("config", "user.email", "nano-codex@example.invalid")
        self._ensure_internal_ignore()
        self._git("add", ".")
        self._git("commit", "-m", "Initialize NanoCodex job")

    def _prepare_source_repository(self) -> str:
        assert self.source_repository is not None
        if not self.source_repository.is_dir():
            raise ValueError("source repository does not exist")
        inside = self._git_at(
            self.source_repository,
            "rev-parse",
            "--is-inside-work-tree",
        )
        if inside.strip() != "true":
            raise ValueError("source repository is not a Git worktree")
        if self._git_at(self.source_repository, "status", "--porcelain").strip():
            raise ValueError("source repository must be clean before NanoCodex starts")
        return self._git_at(self.source_repository, "rev-parse", "HEAD").strip()

    def _clone_source_repository(self) -> None:
        assert self.source_repository is not None
        self.root.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(
            [
                "git",
                "clone",
                "--no-hardlinks",
                str(self.source_repository),
                str(self.repository),
            ],
            cwd=self.root,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
            raise RuntimeError(f"git clone failed: {detail}")
        self._git("config", "user.name", "NanoCodex")
        self._git("config", "user.email", "nano-codex@example.invalid")
        self._ensure_internal_ignore()

    def _deliver(self, state: CodexRunState) -> None:
        assert state.delivery_mode is not None
        state.delivery_status = DeliveryStatus.RUNNING
        state.delivery_error = ""
        self.store.save(state)
        try:
            if state.delivery_mode != DeliveryMode.KEEP:
                state.delivery_commit = self._commit_active_worktree(state)
                self.store.save(state)
            if state.delivery_mode in {DeliveryMode.APPLY, DeliveryMode.MERGE}:
                state.delivery_target_commit = self._deliver_to_source(state)
                self.store.save(state)
            state.delivery_status = DeliveryStatus.COMPLETED
            self.store.save(state)
        except Exception as error:
            state.delivery_error = f"{type(error).__name__}: {error}"
            self.store.save(state)
            raise

    def _commit_active_worktree(self, state: CodexRunState) -> str:
        if not state.active_workspace:
            raise RuntimeError("delivery has no active worktree")
        workspace = Path(state.active_workspace)
        marker = self._delivery_marker(state)
        if state.delivery_commit:
            self._git_at(workspace, "cat-file", "-e", f"{state.delivery_commit}^{{commit}}")
            return state.delivery_commit
        last_message = self._git_at(workspace, "log", "-1", "--format=%B")
        if marker in last_message:
            return self._git_at(workspace, "rev-parse", "HEAD").strip()
        if not state.changed_files:
            raise RuntimeError("delivery cannot commit without changed files")
        approved = set(state.changed_files)
        actual = self._worktree_changed_paths(workspace)
        unexpected = sorted(actual - approved)
        if unexpected:
            raise RuntimeError(
                "active worktree contains changes outside the approved file set: "
                f"{unexpected}"
            )
        self._git_at(workspace, "add", "--", *state.changed_files)
        staged = subprocess.run(
            ["git", "diff", "--cached", "--quiet"],
            cwd=workspace,
        )
        if staged.returncode == 0:
            raise RuntimeError("approved changes do not produce a Git diff")
        if staged.returncode != 1:
            raise RuntimeError("git could not inspect the approved staged changes")
        self._git_at(workspace, "commit", "-m", marker)
        remaining = self._git_at(workspace, "status", "--porcelain").strip()
        if remaining:
            raise RuntimeError(
                "active worktree contains changes outside the approved file set: "
                f"{remaining}"
            )
        return self._git_at(workspace, "rev-parse", "HEAD").strip()

    def _worktree_changed_paths(self, workspace: Path) -> set[str]:
        commands = [
            ("diff", "--name-only", "-z", "--", "."),
            ("diff", "--cached", "--name-only", "-z", "--", "."),
            ("ls-files", "--others", "--exclude-standard", "-z"),
        ]
        paths: set[str] = set()
        for command in commands:
            paths.update(
                path
                for path in self._git_at(workspace, *command).split("\0")
                if path
            )
        return paths

    def _deliver_to_source(self, state: CodexRunState) -> str:
        if not state.source_repository or not state.delivery_commit:
            raise RuntimeError("source delivery requires source and delivery commit")
        source = Path(state.source_repository)
        marker = self._delivery_marker(state)
        last_message = self._git_at(source, "log", "-1", "--format=%B")
        if marker in last_message:
            return self._git_at(source, "rev-parse", "HEAD").strip()
        if self._git_at(source, "status", "--porcelain").strip():
            raise RuntimeError("source repository became dirty before delivery")
        current_head = self._git_at(source, "rev-parse", "HEAD").strip()
        if state.source_head and current_head != state.source_head:
            raise RuntimeError("source repository HEAD changed before delivery")
        self._git_at(
            source,
            "fetch",
            "--no-tags",
            state.repository,
            state.delivery_commit,
        )
        operation = (
            ["cherry-pick", "FETCH_HEAD"]
            if state.delivery_mode == DeliveryMode.APPLY
            else ["merge", "--no-ff", "FETCH_HEAD", "-m", marker]
        )
        try:
            self._git_at(source, *operation)
        except Exception:
            abort = (
                ["cherry-pick", "--abort"]
                if state.delivery_mode == DeliveryMode.APPLY
                else ["merge", "--abort"]
            )
            subprocess.run(
                ["git", *abort],
                cwd=source,
                capture_output=True,
                text=True,
            )
            raise
        return self._git_at(source, "rev-parse", "HEAD").strip()

    @staticmethod
    def _delivery_marker(state: CodexRunState) -> str:
        summary = state.execution_summary.strip() or state.objective.strip()
        return f"[NanoCodex:{state.run_id}] {summary}"

    def _git(self, *arguments: str) -> None:
        self._git_at(self.repository, *arguments)

    @staticmethod
    def _git_at(cwd: Path, *arguments: str) -> str:
        result = subprocess.run(
            ["git", *arguments],
            cwd=cwd,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
            raise RuntimeError(f"git {' '.join(arguments)} failed: {detail}")
        return result.stdout

    def _ensure_internal_ignore(self) -> None:
        ignore = self.repository / ".git" / "info" / "exclude"
        content = ignore.read_text(encoding="utf-8") if ignore.exists() else ""
        lines = content.splitlines()
        if ".nano_codex/" not in lines:
            if content and not content.endswith("\n"):
                content += "\n"
            ignore.parent.mkdir(parents=True, exist_ok=True)
            ignore.write_text(content + ".nano_codex/\n", encoding="utf-8")

    @staticmethod
    def _system_prompt(phase: CodexPhase) -> str:
        return {
            CodexPhase.PLAN: (
                "NanoCodex Plan phase. Use list/read/search/status/diff to inspect, then call "
                "plan_submit exactly once with concrete steps."
            ),
            CodexPhase.EXECUTE: (
                "NanoCodex Execute phase. Work only in the active Git worktree. "
                "Use approved write/patch tools, trusted tests, and call execution_finish."
            ),
            CodexPhase.REVIEW: (
                "NanoCodex Review phase. Inspect the result and call review_submit. "
                "Then call delivery_submit with an allowed mode. Your verdict is advisory; "
                "trusted host evidence is authoritative."
            ),
        }[phase]

    @staticmethod
    def _phase_query(phase: CodexPhase, state: CodexRunState) -> str:
        if phase == CodexPhase.PLAN:
            return state.objective
        if phase == CodexPhase.EXECUTE:
            return (
                f"Execute the persisted plan: {state.plan_steps}. "
                f"Objective: {state.objective}"
            )
        return (
            f"Review the changed files {state.changed_files}. "
            f"Execution summary: {state.execution_summary}"
        )

    def _result(self, state: CodexRunState) -> CodexRunResult:
        return CodexRunResult(
            job=state.job_name,
            run_id=state.run_id,
            status=state.status,
            phase=state.phase,
            success=(
                state.status == CodexStatus.COMPLETED
                and bool(state.evidence)
                and all(record.passed for record in state.evidence)
            ),
            total_steps=state.total_steps,
            tools=sorted(state.tool_counts),
            approvals=list(state.approvals),
            evidence=list(state.evidence),
            artifact=(state.artifacts[-1] if state.artifacts else None),
            state_path=str(self.store.path),
            repository=state.repository,
            active_workspace=state.active_workspace,
            delivery_mode=state.delivery_mode,
            delivery_commit=state.delivery_commit,
            delivery_target_commit=state.delivery_target_commit,
            error=state.error,
        )
