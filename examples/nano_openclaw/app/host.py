"""Durable Ingress -> Turn -> Approval -> Delivery NanoOpenClaw host."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any, List, Optional

from nanoharness.components import (
    DictToolRegistry,
    JsonStateStore,
    JsonlEventSink,
    RegistryToolExecutor,
    SimpleHookManager,
    TraceEvaluator,
)
from nanoharness.extensions import ExtensionContext
from nanoharness.extensions.channels import (
    DurableChannelGateway,
    InboxRecord,
    InboxStatus,
    OutboundEnvelope,
    OutboxStatus,
)
from nanoharness.profiles import HarnessBuild, HarnessBuilder, HarnessSpec, HostRequirements
from nanoharness.testing import (
    RunArtifactStore,
    ScriptedLLM,
    ScriptedResponse,
    bind_profile_paths,
)

from app.approvals import OutboundDecider, decide_outbound
from app.context import build_turn_context
from app.models import (
    ConversationExchange,
    ConversationRoute,
    GatewayBatchResult,
    GatewayJob,
    GatewayRunResult,
    GatewayTurnState,
    OutboundApproval,
    TurnPhase,
    TurnStatus,
    content_sha256,
    stable_turn_id,
    utc_now,
)
from app.policy import GatewayPolicy
from app.store import ConversationStore, GatewayTurnStore
from app.tools import GatewayToolRuntime, register_gateway_tools


ProviderFactory = Callable[[GatewayTurnState, List[ScriptedResponse]], Any]


class GatewayHost:
    """Application control plane composed around the public Channel Gateway."""

    def __init__(
        self,
        job: GatewayJob | str | Path,
        root: str | Path,
        *,
        resume_run_id: Optional[str] = None,
        profile_path: Optional[str | Path] = None,
        provider_factory: Optional[ProviderFactory] = None,
        approve_outbound: OutboundDecider = True,
    ):
        self.job = job if isinstance(job, GatewayJob) else GatewayJob.from_file(job)
        self.root = Path(root).resolve()
        self.workspace = self.root / "workspace"
        self.runtime_root = self.root / "runtime"
        self.runs_root = self.runtime_root / "runs"
        self.engine_root = self.runtime_root / "engine"
        self.artifact_root = self.root / "artifacts"
        self.conversation_store = ConversationStore(
            self.runtime_root / "conversations.json"
        )
        self.artifacts = RunArtifactStore(self.artifact_root)
        example_root = Path(__file__).resolve().parents[1]
        self.profile_path = Path(profile_path or example_root / "profile.yaml").resolve()
        self.resume_run_id = resume_run_id
        self.provider_factory = provider_factory
        self.approve_outbound = approve_outbound
        self._profile: Optional[HarnessSpec] = None
        self._extension_build: Optional[HarnessBuild] = None
        self._gateway: Optional[DurableChannelGateway] = None

    @property
    def gateway(self) -> DurableChannelGateway:
        self._ensure_runtime()
        assert self._gateway is not None
        return self._gateway

    def close(self) -> None:
        if self._extension_build is not None:
            self._extension_build.close()
            self._extension_build = None
            self._gateway = None

    def __enter__(self) -> "GatewayHost":
        self._ensure_runtime()
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()

    def run(self) -> GatewayBatchResult | GatewayRunResult:
        if self.resume_run_id:
            return self.resume(self.resume_run_id)
        return self.run_job()

    def run_job(self) -> GatewayBatchResult:
        self._ensure_runtime()
        results = []
        for message in self.job.messages:
            record, _ = self.gateway.ingest(message.envelope)
            results.append(self.process_inbox(record.id))
        return GatewayBatchResult(
            job=self.job.name,
            success=all(result.success for result in results),
            processed=len(results),
            delivered=sum(
                result.delivery_status == OutboxStatus.SENT for result in results
            ),
            turns=results,
        )

    def ingest(self, envelope) -> tuple[InboxRecord, bool]:
        return self.gateway.ingest(envelope)

    def run_pending(self, *, limit: Optional[int] = None) -> List[GatewayRunResult]:
        if limit is not None and limit < 0:
            raise ValueError("limit must be non-negative")
        results = []
        while limit is None or len(results) < limit:
            claimed = self.gateway.claim_next("nano-openclaw-pending")
            if claimed is None:
                break
            results.append(self._process_claimed(claimed))
        return results

    def resume(self, run_id: str) -> GatewayRunResult:
        store = self._turn_store(run_id)
        if not store.exists():
            raise ValueError(f"NanoOpenClaw turn not found: {run_id}")
        state = store.load()
        if state.job_name != self.job.name:
            raise ValueError("persisted NanoOpenClaw turn belongs to a different job")
        return self.process_inbox(state.inbox_id)

    def process_inbox(self, inbox_id: str) -> GatewayRunResult:
        record = self.gateway.store.get_inbox(inbox_id)
        if record is None:
            raise ValueError(f"channel inbox not found: {inbox_id}")
        run_id = stable_turn_id(inbox_id)
        store = self._turn_store(run_id)
        if record.status == InboxStatus.COMPLETED:
            if not store.exists():
                raise RuntimeError("completed inbox has no NanoOpenClaw turn state")
            state = store.load()
            if state.status != TurnStatus.COMPLETED:
                state.phase = TurnPhase.COMPLETED
                state.status = TurnStatus.COMPLETED
                state.claim_token = None
                state.error = ""
                store.save(state)
            return self._result(state, store)
        if record.status == InboxStatus.RECEIVED:
            record = self.gateway.claim_inbox(
                inbox_id,
                f"nano-openclaw:{run_id}",
            )
        elif record.status == InboxStatus.FAILED:
            raise RuntimeError(f"inbox is terminally failed: {inbox_id}")
        elif store.exists():
            persisted = store.load()
            if persisted.claim_token != record.claim_token:
                raise RuntimeError("inbox is claimed by a different host attempt")
        else:
            raise RuntimeError("claimed inbox has no resumable turn state")
        return self._process_claimed(record)

    def _process_claimed(self, record: InboxRecord) -> GatewayRunResult:
        if record.claim_token is None:
            raise RuntimeError("claimed inbox is missing its claim token")
        store = self._turn_store(stable_turn_id(record.id))
        state = self._load_or_create_state(record, store)
        state.claim_token = record.claim_token
        state.status = TurnStatus.RUNNING
        state.error = ""
        store.save(state)
        try:
            if state.phase == TurnPhase.RESPOND:
                self._run_response(state, store)
            if state.phase == TurnPhase.RESPOND:
                state.status = TurnStatus.BLOCKED
                state.error = "model run ended without response_submit"
                store.save(state)
                self._release_for_retry(state, store)
                return self._result(state, store)
            if state.phase == TurnPhase.DELIVERY:
                self._review_and_deliver(state, store)
            return self._result(state, store)
        except Exception as error:
            state.status = TurnStatus.INTERRUPTED
            state.error = f"{type(error).__name__}: {error}"
            store.save(state)
            self._release_for_retry(state, store)
            return self._result(state, store)

    def _load_or_create_state(
        self,
        record: InboxRecord,
        store: GatewayTurnStore,
    ) -> GatewayTurnState:
        route = ConversationRoute.from_envelope(record.envelope)
        conversation = self.conversation_store.resolve(route)
        message = self.job.message_for(record.envelope)
        fingerprint = (
            message.fingerprint() if message is not None else record.payload_fingerprint
        )
        if store.exists():
            state = store.load()
            if (
                state.inbox_id != record.id
                or state.message_fingerprint != fingerprint
                or Path(state.workspace).resolve() != self.workspace
            ):
                raise ValueError("persisted NanoOpenClaw turn does not match this input")
            return state
        state = GatewayTurnState(
            run_id=stable_turn_id(record.id),
            job_name=self.job.name,
            message_fingerprint=fingerprint,
            inbox_id=record.id,
            claim_token=record.claim_token,
            route=route,
            conversation_id=conversation.conversation_id,
            session_id=conversation.session_id,
            external_message_id=record.envelope.message_id,
            user_content=record.envelope.content,
            workspace=str(self.workspace),
        )
        store.save(state)
        return state

    def _run_response(self, state: GatewayTurnState, store: GatewayTurnStore) -> None:
        assert self._profile is not None
        message_record = self.gateway.store.get_inbox(state.inbox_id)
        assert message_record is not None
        job_message = self.job.message_for(message_record.envelope)
        responses = job_message.responses if job_message is not None else []
        if self.provider_factory is None:
            if not responses:
                raise ValueError(
                    "a provider_factory is required for an unscripted inbound message"
                )
            provider = ScriptedLLM(responses)
        else:
            provider = self.provider_factory(state, responses)

        conversation = self.conversation_store.get(state.conversation_id)
        if conversation is None:
            raise RuntimeError("turn conversation was not persisted")
        tools = DictToolRegistry()
        register_gateway_tools(
            tools,
            GatewayToolRuntime(state, store, self.workspace),
        )
        binding = self._profile.engine
        if binding is None:
            raise ValueError("NanoOpenClaw profile must declare an engine")
        state.engine_attempts += 1
        attempt_id = f"{state.run_id}_respond_{state.engine_attempts}"
        phase_root = self.engine_root / state.run_id / str(state.engine_attempts)
        phase_root.mkdir(parents=True, exist_ok=True)
        services = {
            binding.llm_service: provider,
            binding.context_service: build_turn_context(conversation),
            binding.state_service: JsonStateStore(str(phase_root / "checkpoint.json")),
            binding.hooks_service: SimpleHookManager(),
            binding.evaluator_service: TraceEvaluator(),
        }
        if binding.policy_service:
            services[binding.policy_service] = GatewayPolicy(state)
        if binding.executor_service:
            services[binding.executor_service] = RegistryToolExecutor(tools)
        if binding.event_sink_service:
            services[binding.event_sink_service] = JsonlEventSink(
                str(phase_root / "events.jsonl")
            )
        context = ExtensionContext(
            tools=tools,
            services=services,
            capabilities=set(self._extension_build.context.capabilities),
            metadata={
                "workspace_root": str(self.workspace),
                "run_id": state.run_id,
                "conversation_id": state.conversation_id,
            },
        )
        runtime_spec = self._profile.model_copy(update={"extensions": []})
        build = HarnessBuilder().build(runtime_spec, context=context)
        store.save(state)
        try:
            if build.engine is None:
                raise RuntimeError("NanoOpenClaw profile did not bind NanoEngine")
            report = build.engine.run(
                state.user_content,
                run_id=attempt_id,
                session_id=state.session_id,
            )
            artifact, trace = self.artifacts.save(
                profile=self._profile.name,
                scenario=f"{self.job.name}-turn",
                report=report,
            )
            state.artifacts.append(artifact)
            state.total_steps += trace.total_steps
            for name, count in trace.tool_counts.items():
                state.tool_counts[name] = state.tool_counts.get(name, 0) + count
            store.save(state)
        finally:
            build.close()

    def _review_and_deliver(
        self,
        state: GatewayTurnState,
        store: GatewayTurnStore,
    ) -> None:
        envelope = OutboundEnvelope(
            channel=state.route.channel,
            account_id=state.route.account_id,
            conversation_id=state.route.conversation_id,
            recipient_id=state.route.sender_id,
            content=state.response,
            reply_to_message_id=state.external_message_id,
            metadata={"source": "nano-openclaw", "run_id": state.run_id},
        )
        if state.outbox_id is None:
            outbox, _ = self.gateway.queue_outbound(
                envelope,
                idempotency_key=f"nano-openclaw:{state.run_id}:response",
            )
            state.outbox_id = outbox.id
            state.delivery_status = outbox.status
            store.save(state)
        outbox = self.gateway.store.get_outbox(state.outbox_id)
        if outbox is None:
            raise RuntimeError("persisted turn references a missing outbox record")

        if state.approval is None:
            approved, reason = decide_outbound(self.approve_outbound, envelope)
            state.approval = OutboundApproval(
                outbox_id=outbox.id,
                approved=approved,
                reason=reason,
                channel=envelope.channel,
                account_id=envelope.account_id,
                conversation_id=envelope.conversation_id,
                recipient_id=envelope.recipient_id,
                content_sha256=content_sha256(envelope.content),
                content_length=len(envelope.content),
            )
            store.save(state)

        if outbox.status == OutboxStatus.PENDING:
            outbox = (
                self.gateway.approve_outbox(outbox.id)
                if state.approval.approved
                else self.gateway.reject_outbox(
                    outbox.id,
                    reason=state.approval.reason,
                )
            )
        if outbox.status == OutboxStatus.REJECTED:
            self._complete_turn(state, store, outbox.status, delivered=False)
            return
        if not state.approval.approved:
            raise RuntimeError("rejected approval conflicts with deliverable outbox state")
        if outbox.status == OutboxStatus.FAILED:
            outbox = self.gateway.retry_outbox(outbox.id)
        if outbox.status == OutboxStatus.APPROVED:
            outbox = self.gateway.deliver(outbox.id)
        state.delivery_status = outbox.status
        store.save(state)
        if outbox.status != OutboxStatus.SENT:
            raise RuntimeError(outbox.last_error or "channel delivery failed")
        self._complete_turn(state, store, outbox.status, delivered=True)

    def _complete_turn(
        self,
        state: GatewayTurnState,
        store: GatewayTurnStore,
        delivery_status: OutboxStatus,
        *,
        delivered: bool,
    ) -> None:
        if state.completed_at is None:
            state.completed_at = utc_now()
            store.save(state)
        self.conversation_store.commit(
            state.conversation_id,
            ConversationExchange(
                turn_id=state.run_id,
                inbox_id=state.inbox_id,
                external_message_id=state.external_message_id,
                user_content=state.user_content,
                assistant_content=state.response,
                delivered=delivered,
                outbox_id=state.outbox_id,
                completed_at=state.completed_at,
            ),
        )
        inbox = self.gateway.store.get_inbox(state.inbox_id)
        if inbox is not None and inbox.status != InboxStatus.COMPLETED:
            if state.claim_token is None:
                raise RuntimeError("turn lost its inbox claim before completion")
            self.gateway.complete_inbox(
                state.inbox_id,
                state.claim_token,
                run_id=state.run_id,
            )
        state.delivery_status = delivery_status
        state.phase = TurnPhase.COMPLETED
        state.status = TurnStatus.COMPLETED
        state.claim_token = None
        state.error = ""
        store.save(state)

    def _release_for_retry(
        self,
        state: GatewayTurnState,
        store: GatewayTurnStore,
    ) -> None:
        inbox = self.gateway.store.get_inbox(state.inbox_id)
        if (
            inbox is not None
            and inbox.status == InboxStatus.CLAIMED
            and state.claim_token
            and inbox.claim_token == state.claim_token
        ):
            try:
                self.gateway.fail_inbox(
                    state.inbox_id,
                    state.claim_token,
                    error=state.error or "NanoOpenClaw turn requires retry",
                    retryable=True,
                )
            except Exception:
                self.gateway.store.recover_expired_claims()
        state.claim_token = None
        store.save(state)

    def _ensure_runtime(self) -> None:
        if self._extension_build is not None:
            return
        for path in (
            self.workspace,
            self.runtime_root,
            self.runs_root,
            self.engine_root,
            self.artifact_root,
        ):
            path.mkdir(parents=True, exist_ok=True)
        self.job.materialize(self.workspace)
        spec = HarnessSpec.from_file(str(self.profile_path))
        bound = bind_profile_paths(spec, {
            "workspace": str(self.workspace),
            "runtime": str(self.runtime_root),
        })
        validation = HarnessBuilder().validate(bound)
        if not validation.valid:
            details = "; ".join(issue.message for issue in validation.errors)
            raise ValueError(f"invalid NanoOpenClaw profile: {details}")
        extension_spec = bound.model_copy(update={
            "engine": None,
            "host": HostRequirements(
                capabilities=list(bound.host.capabilities),
                services=[],
            ),
        })
        extension_context = ExtensionContext(
            tools=DictToolRegistry(),
            capabilities=set(bound.host.capabilities),
            metadata={"workspace_root": str(self.workspace)},
        )
        build = HarnessBuilder().build(extension_spec, context=extension_context)
        gateway = build.context.services.get("channels")
        if not isinstance(gateway, DurableChannelGateway):
            build.close()
            raise RuntimeError("NanoOpenClaw profile did not install channels.durable")
        self._profile = bound
        self._extension_build = build
        self._gateway = gateway

    def _turn_store(self, run_id: str) -> GatewayTurnStore:
        return GatewayTurnStore(self.runs_root / f"{run_id}.json")

    def _result(
        self,
        state: GatewayTurnState,
        store: GatewayTurnStore,
    ) -> GatewayRunResult:
        return GatewayRunResult(
            job=state.job_name,
            run_id=state.run_id,
            inbox_id=state.inbox_id,
            conversation_id=state.conversation_id,
            session_id=state.session_id,
            status=state.status,
            phase=state.phase,
            success=state.status == TurnStatus.COMPLETED,
            response=state.response,
            delivery_status=state.delivery_status,
            outbox_id=state.outbox_id,
            approval=state.approval,
            total_steps=state.total_steps,
            tools=sorted(state.tool_counts),
            artifact=(state.artifacts[-1] if state.artifacts else None),
            artifacts=list(state.artifacts),
            state_path=str(store.path),
            conversation_path=str(self.conversation_store.path),
            workspace=state.workspace,
            error=state.error,
        )
