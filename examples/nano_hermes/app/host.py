"""Resumable Assist -> Reflect -> Review host for persistent NanoHermes runs."""

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
from nanoharness.extensions.memory import FileMemoryManager
from nanoharness.extensions.scheduler import Scheduler
from nanoharness.extensions.skills import SkillRegistry
from nanoharness.profiles import HarnessBuilder, HarnessSpec
from nanoharness.testing import (
    RunArtifactStore,
    ScriptedLLM,
    ScriptedResponse,
    bind_profile_paths,
)

from app.approvals import (
    ActionDecider,
    LearningDecider,
    RecordingActionApprovalBroker,
)
from app.learning import LearningReviewer
from app.models import (
    HermesJob,
    HermesPhase,
    HermesRunKind,
    HermesRunResult,
    HermesRunState,
    HermesStatus,
    HermesTransition,
    ProposalStatus,
)
from app.policy import HermesPolicy
from app.store import HermesRunStore
from app.tools import HermesToolRuntime, register_hermes_tools


ProviderFactory = Callable[[HermesPhase, List[ScriptedResponse]], Any]


class HermesHost:
    """Application-owned persistent learning orchestration."""

    def __init__(
        self,
        job: HermesJob | str | Path,
        root: str | Path,
        *,
        resume_run_id: Optional[str] = None,
        profile_path: Optional[str | Path] = None,
        seed_skills_root: Optional[str | Path] = None,
        provider_factory: Optional[ProviderFactory] = None,
        approve_actions: ActionDecider = True,
        approve_learning: LearningDecider = True,
    ):
        self.job = job if isinstance(job, HermesJob) else HermesJob.from_file(job)
        self.root = Path(root).resolve()
        self.workspace = self.root / "workspace"
        self.runtime_root = self.root / "runtime"
        self.memory_root = self.runtime_root / "memory"
        self.skills_root = self.runtime_root / "skills"
        self.runs_root = self.runtime_root / "runs"
        self.staging_root = self.runtime_root / "staged"
        self.artifact_root = self.root / "artifacts"
        example_root = Path(__file__).resolve().parents[1]
        self.profile_path = Path(profile_path or example_root / "profile.yaml").resolve()
        self.seed_skills_root = Path(
            seed_skills_root or example_root / "skills"
        ).resolve()
        self.resume_run_id = resume_run_id
        if provider_factory is None:
            if not self.job.scripted:
                raise ValueError(
                    "a provider_factory is required when the job has no scripted phases"
                )
            provider_factory = lambda _phase, responses: ScriptedLLM(responses)
        self.provider_factory = provider_factory
        self.approve_actions = approve_actions
        self.approve_learning = approve_learning
        self.store: Optional[HermesRunStore] = None
        self.artifacts = RunArtifactStore(self.artifact_root)

    def run(self) -> HermesRunResult:
        state = self._load_or_create_state()
        assert self.store is not None
        if state.phase == HermesPhase.COMPLETED:
            return self._result(state)

        for _ in range(3):
            started_phase = state.phase
            state.status = HermesStatus.RUNNING
            state.error = ""
            self.store.save(state)
            try:
                if started_phase in {HermesPhase.ASSIST, HermesPhase.REFLECT}:
                    self._run_phase(state, started_phase)
                elif started_phase == HermesPhase.REVIEW:
                    self._review_and_complete(state)
            except Exception as error:
                state.status = HermesStatus.INTERRUPTED
                state.error = f"{type(error).__name__}: {error}"
                self.store.save(state)
                return self._result(state)

            if state.status in {
                HermesStatus.BLOCKED,
                HermesStatus.COMPLETED,
                HermesStatus.INTERRUPTED,
            }:
                return self._result(state)
            if state.phase == started_phase:
                state.status = HermesStatus.BLOCKED
                state.error = (
                    f"{started_phase.value} phase ended without its required "
                    "host transition tool"
                )
                self.store.save(state)
                return self._result(state)
        state.status = HermesStatus.INTERRUPTED
        state.error = "phase transition budget exhausted"
        self.store.save(state)
        return self._result(state)

    def run_due(self, template_job: Optional[HermesJob] = None) -> List[HermesRunResult]:
        """Run each newly due persisted schedule as an independent Hermes run."""

        results = []
        for notice in self.collect_due(self.root):
            base = template_job or self.job
            scheduled_job = base.model_copy(update={
                "name": f"scheduled-{notice['schedule_id']}-{notice['fire_count']}",
                "query": notice["prompt"],
                "run_kind": HermesRunKind.SCHEDULED,
                "schedule_id": notice["schedule_id"],
                "fixture_files": {},
            })
            results.append(HermesHost(
                scheduled_job,
                self.root,
                profile_path=self.profile_path,
                seed_skills_root=self.seed_skills_root,
                provider_factory=self.provider_factory,
                approve_actions=self.approve_actions,
                approve_learning=self.approve_learning,
            ).run())
        return results

    @staticmethod
    def collect_due(root: str | Path) -> List[dict]:
        scheduler = Scheduler(
            persist_path=str(Path(root).resolve() / "runtime" / "schedules.json"),
            start_checker=False,
        )
        try:
            return scheduler.check_due()
        finally:
            scheduler.stop()

    def _load_or_create_state(self) -> HermesRunState:
        self._prepare_persistent_roots()
        if self.resume_run_id:
            self.store = HermesRunStore(
                self.runs_root / f"{self.resume_run_id}.json"
            )
            if not self.store.exists():
                raise ValueError(f"NanoHermes run not found: {self.resume_run_id}")
            state = self.store.load()
            if (
                state.job_name != self.job.name
                or state.job_fingerprint != self.job.fingerprint()
            ):
                raise ValueError(
                    "persisted NanoHermes run belongs to a different job"
                )
            if Path(state.workspace).resolve() != self.workspace.resolve():
                raise ValueError("persisted NanoHermes workspace does not match this host")
            return state

        self.job.materialize(self.workspace)
        state = HermesRunState(
            job_name=self.job.name,
            job_fingerprint=self.job.fingerprint(),
            query=self.job.query,
            run_kind=self.job.run_kind,
            schedule_id=self.job.schedule_id,
            workspace=str(self.workspace.resolve()),
        )
        self.store = HermesRunStore(self.runs_root / f"{state.run_id}.json")
        self.store.save(state)
        return state

    def _prepare_persistent_roots(self) -> None:
        for path in (
            self.workspace,
            self.memory_root,
            self.skills_root,
            self.runs_root,
            self.staging_root,
            self.artifact_root,
        ):
            path.mkdir(parents=True, exist_ok=True)
        if self.seed_skills_root.is_dir():
            for source in sorted(self.seed_skills_root.glob("*.md")):
                target = self.skills_root / source.name
                if not target.exists():
                    target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")

    def _run_phase(self, state: HermesRunState, phase: HermesPhase) -> None:
        assert self.store is not None
        spec = HarnessSpec.from_file(str(self.profile_path))
        bound = bind_profile_paths(spec, {
            "workspace": str(self.workspace),
            "runtime": str(self.runtime_root),
            "skills": str(self.skills_root),
        })
        tools = DictToolRegistry()
        services = self._services(state, phase, tools)
        context = ExtensionContext(
            tools=tools,
            services=services,
            capabilities=set(bound.host.capabilities),
            metadata={
                "workspace_root": str(self.workspace),
                "run_id": state.run_id,
                "phase": phase.value,
            },
        )
        register_hermes_tools(
            context,
            HermesToolRuntime(
                state=state,
                store=self.store,
                context=context,
                workspace=self.workspace,
                memory_root=self.memory_root,
                skills_root=self.skills_root,
                staging_root=self.staging_root / state.run_id,
            ),
        )
        build = HarnessBuilder().build(bound, context=context)
        try:
            if build.engine is None:
                raise RuntimeError("NanoHermes profile did not bind NanoEngine")
            report = build.engine.run(
                self._phase_query(phase, state),
                run_id=f"{state.run_id}_{phase.value}_{len(state.artifacts) + 1}",
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
        finally:
            build.close()

    def _services(
        self,
        state: HermesRunState,
        phase: HermesPhase,
        tools: DictToolRegistry,
    ) -> dict[str, Any]:
        assert self.store is not None
        binding = HarnessSpec.from_file(str(self.profile_path)).engine
        if binding is None:
            raise ValueError("NanoHermes profile must declare an engine")
        lifecycle = {
            "policy_service": binding.policy_service,
            "approval_broker_service": binding.approval_broker_service,
            "executor_service": binding.executor_service,
            "event_sink_service": binding.event_sink_service,
        }
        missing = [name for name, service in lifecycle.items() if not service]
        if missing:
            raise ValueError(
                "NanoHermes profile is missing controlled lifecycle bindings: "
                f"{missing}"
            )
        phase_runtime = self.runs_root / state.run_id / phase.value
        phase_runtime.mkdir(parents=True, exist_ok=True)
        provider = self.provider_factory(phase, self.job.phases.get(phase, []))
        return {
            binding.llm_service: provider,
            binding.context_service: SimpleContextManager(
                system_prompt=self._system_prompt(phase, state)
            ),
            binding.state_service: JsonStateStore(
                str(phase_runtime / "checkpoint.json")
            ),
            binding.hooks_service: SimpleHookManager(),
            binding.evaluator_service: TraceEvaluator(),
            binding.policy_service: HermesPolicy(state),
            binding.approval_broker_service: RecordingActionApprovalBroker(
                state,
                self.store,
                self.approve_actions,
            ),
            binding.executor_service: RegistryToolExecutor(tools),
            binding.event_sink_service: JsonlEventSink(
                str(phase_runtime / "events.jsonl")
            ),
        }

    def _review_and_complete(self, state: HermesRunState) -> None:
        assert self.store is not None
        LearningReviewer(
            self.memory_root,
            self.skills_root,
            self.approve_learning,
            staging_root=self.staging_root / state.run_id,
        ).review(state, self.store)
        state.transitions.append(HermesTransition(
            source=HermesPhase.REVIEW,
            target=HermesPhase.COMPLETED,
            reason="host learning review completed",
        ))
        state.phase = HermesPhase.COMPLETED
        state.status = HermesStatus.COMPLETED
        state.error = ""
        self.store.save(state)

    def _system_prompt(self, phase: HermesPhase, state: HermesRunState) -> str:
        memory = FileMemoryManager(str(self.memory_root)).load_for_injection()
        skills = SkillRegistry(str(self.skills_root)).discover_text()
        durable_context = (
            "\n\nDurable memory index:\n" + (memory or "(empty)")
            + "\n\nAvailable skills:\n" + (skills or "(none)")
        )
        if phase == HermesPhase.ASSIST:
            return (
                "NanoHermes Assist phase. Help with the query using read-only recall, "
                "skills, controlled workspace actions, schedules, or isolated delegation. "
                "Durable learning cannot be written directly. Finish with assist_submit."
                + durable_context
            )
        return (
            "NanoHermes Reflect phase. Review the response and propose only genuinely "
            "durable memory or reusable procedural skills. Proposals are staged and the "
            "host decides promotion. Finish with reflection_submit."
            + durable_context
        )

    @staticmethod
    def _phase_query(phase: HermesPhase, state: HermesRunState) -> str:
        if phase == HermesPhase.ASSIST:
            return state.query
        return (
            f"Reflect on query: {state.query}\n\n"
            f"Submitted response: {state.response}"
        )

    def _result(self, state: HermesRunState) -> HermesRunResult:
        assert self.store is not None
        return HermesRunResult(
            job=state.job_name,
            run_id=state.run_id,
            run_kind=state.run_kind,
            status=state.status,
            phase=state.phase,
            success=state.status == HermesStatus.COMPLETED,
            response=state.response,
            promoted=[
                f"{proposal.kind.value}:{proposal.name}"
                for proposal in state.proposals
                if proposal.status == ProposalStatus.PROMOTED
            ],
            rejected=[
                f"{proposal.kind.value}:{proposal.name}"
                for proposal in state.proposals
                if proposal.status in {
                    ProposalStatus.REJECTED,
                    ProposalStatus.INVALID,
                }
            ],
            total_steps=state.total_steps,
            tools=sorted(state.tool_counts),
            action_approvals=list(state.action_approvals),
            decisions=list(state.decisions),
            artifact=(state.artifacts[-1] if state.artifacts else None),
            artifacts=list(state.artifacts),
            state_path=str(self.store.path),
            workspace=state.workspace,
            error=state.error,
        )
