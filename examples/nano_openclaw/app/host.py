"""Durable Wakeup -> Turn -> Outbox -> Approval -> Delivery host."""

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
from nanoharness.extensions import ExtensionContext, NotificationSourceProtocol
from nanoharness.extensions.channels import (
    DurableChannelGateway,
    InboxRecord,
    InboxStatus,
    OutboundEnvelope,
    OutboxStatus,
)
from nanoharness.extensions.scheduler import Scheduler
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
    WakeupEnvelope,
    WakeupRecord,
    WakeupSource,
    WakeupStatus,
    WakeupTrust,
    content_sha256,
    stable_turn_id,
    utc_now,
)
from app.policy import GatewayPolicy
from app.store import ConversationStore, GatewayTurnStore, WakeupStore
from app.tools import GatewayToolRuntime, register_gateway_tools
from app.wakeups import (
    background_wakeup,
    channel_wakeup,
    manual_wakeup,
    schedule_wakeup,
)


ProviderFactory = Callable[[GatewayTurnState, List[ScriptedResponse]], Any]


class GatewayHost:
    """Application control plane composed around public Channel and Scheduler data."""

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
        self.wakeup_store = WakeupStore(
            self.runtime_root / "wakeups.json",
            claim_lease_seconds=3600,
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
        self._scheduler: Optional[Scheduler] = None

    @property
    def gateway(self) -> DurableChannelGateway:
        self._ensure_runtime()
        assert self._gateway is not None
        return self._gateway

    @property
    def scheduler(self) -> Scheduler:
        self._ensure_runtime()
        assert self._scheduler is not None
        return self._scheduler

    def close(self) -> None:
        if self._extension_build is not None:
            self._extension_build.close()
            self._extension_build = None
            self._gateway = None
            self._scheduler = None

    def __enter__(self) -> "GatewayHost":
        self._ensure_runtime()
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()

    def run(self) -> GatewayBatchResult | GatewayRunResult:
        if self.resume_run_id:
            return self.resume(self.resume_run_id)
        return self.run_job()

    def run_job(self, *, deliver: bool = True) -> GatewayBatchResult:
        self._ensure_runtime()
        results = []
        for message in self.job.messages:
            inbox, _ = self.ingest(message.envelope)
            results.append(self.process_inbox(inbox.id, deliver=deliver))
        return self._batch(results)

    def ingest(self, envelope) -> tuple[InboxRecord, bool]:
        inbox, created = self.gateway.ingest(envelope)
        self.wakeup_store.ingest(channel_wakeup(inbox))
        return inbox, created

    def ingest_wakeup(
        self,
        envelope: WakeupEnvelope,
    ) -> tuple[WakeupRecord, bool]:
        return self.wakeup_store.ingest(envelope)

    def ingest_manual(
        self,
        content: str,
        route: ConversationRoute,
        *,
        event_id: str,
        metadata: Optional[dict[str, Any]] = None,
    ) -> tuple[WakeupRecord, bool]:
        return self.ingest_wakeup(
            manual_wakeup(
                content,
                route,
                event_id=event_id,
                metadata=metadata,
            )
        )

    def install_job_schedules(self) -> list[dict[str, Any]]:
        installed = []
        existing = self.scheduler.list()
        for scheduled in self.job.schedules:
            match = next(
                (
                    item
                    for item in existing
                    if (item.get("metadata") or {}).get("openclaw_job")
                    == self.job.name
                    and (item.get("metadata") or {}).get("script_key")
                    == scheduled.name
                ),
                None,
            )
            if match is not None:
                installed.append(match)
                continue
            metadata = {
                **scheduled.metadata,
                "openclaw_job": self.job.name,
                "script_key": scheduled.name,
                "route": scheduled.route.model_dump(mode="json"),
            }
            created = self.scheduler.create(
                prompt=scheduled.prompt,
                cron=scheduled.cron,
                delay_seconds=scheduled.delay_seconds,
                max_fires=scheduled.max_fires,
                metadata=metadata,
            )
            existing.append(created)
            installed.append(created)
        return installed

    def collect_due(self) -> list[WakeupRecord]:
        records = []
        for notice in self.scheduler.check_due():
            record, _ = self.ingest_wakeup(schedule_wakeup(notice))
            records.append(record)
        return records

    def run_due(self, *, deliver: bool = True) -> GatewayBatchResult:
        self.install_job_schedules()
        records = self.collect_due()
        results = [
            self.process_wakeup(record.id, deliver=deliver)
            for record in records
        ]
        return self._batch(results)

    def collect_notifications(
        self,
        source: NotificationSourceProtocol,
        route: ConversationRoute,
        *,
        source_instance: str = "background",
    ) -> list[WakeupRecord]:
        records = []
        for notice in source.drain():
            record, _ = self.ingest_wakeup(
                background_wakeup(
                    notice,
                    route,
                    source_instance=source_instance,
                )
            )
            records.append(record)
        return records

    def run_pending(
        self,
        *,
        limit: Optional[int] = None,
        deliver: bool = True,
    ) -> List[GatewayRunResult]:
        if limit is not None and limit < 0:
            raise ValueError("limit must be non-negative")
        self._sync_channel_inbox()
        results = []
        while limit is None or len(results) < limit:
            claimed = self.wakeup_store.claim_next("nano-openclaw-pending")
            if claimed is None:
                break
            results.append(self._process_claimed(claimed, deliver=deliver))
        return results

    def resume(self, run_id: str, *, deliver: bool = True) -> GatewayRunResult:
        store = self._turn_store(run_id)
        if not store.exists():
            raise ValueError(f"NanoOpenClaw turn not found: {run_id}")
        state = store.load()
        if state.job_name != self.job.name:
            raise ValueError("persisted NanoOpenClaw turn belongs to a different job")
        if state.phase == TurnPhase.COMPLETED:
            return self._result(state, store)
        if state.phase == TurnPhase.DELIVERY and self._input_completed(state):
            return self.deliver_turn(run_id) if deliver else self._result(state, store)
        assert state.wakeup_id is not None
        return self.process_wakeup(state.wakeup_id, deliver=deliver)

    def process_inbox(
        self,
        inbox_id: str,
        *,
        deliver: bool = True,
    ) -> GatewayRunResult:
        inbox = self.gateway.store.get_inbox(inbox_id)
        if inbox is None:
            raise ValueError(f"channel inbox not found: {inbox_id}")
        self.wakeup_store.ingest(channel_wakeup(inbox))
        return self.process_wakeup(inbox_id, deliver=deliver)

    def process_wakeup(
        self,
        wakeup_id: str,
        *,
        deliver: bool = True,
    ) -> GatewayRunResult:
        record = self.wakeup_store.get(wakeup_id)
        if record is None:
            raise ValueError(f"wakeup not found: {wakeup_id}")
        store = self._turn_store(stable_turn_id(wakeup_id))
        if record.status == WakeupStatus.COMPLETED:
            if not store.exists():
                raise RuntimeError("completed wakeup has no NanoOpenClaw turn state")
            state = store.load()
            if state.phase == TurnPhase.DELIVERY and state.status not in {
                TurnStatus.WAITING,
                TurnStatus.INTERRUPTED,
            }:
                state.status = TurnStatus.WAITING
                state.claim_token = None
                state.wakeup_claim_token = None
                store.save(state)
            if state.phase == TurnPhase.DELIVERY and deliver:
                return self.deliver_turn(state.run_id)
            return self._result(state, store)
        if record.status == WakeupStatus.PENDING:
            record = self.wakeup_store.claim(
                wakeup_id,
                f"nano-openclaw:{stable_turn_id(wakeup_id)}",
            )
        elif record.status == WakeupStatus.FAILED:
            raise RuntimeError(f"wakeup is terminally failed: {wakeup_id}")
        elif store.exists():
            persisted = store.load()
            if persisted.wakeup_claim_token != record.claim_token:
                raise RuntimeError("wakeup is claimed by a different host attempt")
        else:
            raise RuntimeError("claimed wakeup has no resumable turn state")
        return self._process_claimed(record, deliver=deliver)

    def deliver_pending(self, *, limit: Optional[int] = None) -> list[GatewayRunResult]:
        if limit is not None and limit < 0:
            raise ValueError("limit must be non-negative")
        results = []
        for path in sorted(self.runs_root.glob("*.json")):
            state = GatewayTurnStore(path).load()
            if state.phase != TurnPhase.DELIVERY:
                continue
            if state.status not in {TurnStatus.WAITING, TurnStatus.INTERRUPTED}:
                continue
            results.append(self.deliver_turn(state.run_id))
            if limit is not None and len(results) >= limit:
                break
        return results

    def deliver_turn(self, run_id: str) -> GatewayRunResult:
        store = self._turn_store(run_id)
        if not store.exists():
            raise ValueError(f"NanoOpenClaw turn not found: {run_id}")
        state = store.load()
        if state.phase == TurnPhase.COMPLETED:
            return self._result(state, store)
        if state.phase != TurnPhase.DELIVERY or state.outbox_id is None:
            raise ValueError("turn has no queued outbound response")
        try:
            state.status = TurnStatus.RUNNING
            state.error = ""
            store.save(state)
            self._review_and_deliver(state, store)
        except Exception as error:
            state.status = TurnStatus.INTERRUPTED
            state.error = f"{type(error).__name__}: {error}"
            store.save(state)
        return self._result(state, store)

    def list_pending(self) -> dict[str, list[dict[str, Any]]]:
        self._sync_channel_inbox()
        wakeups = [
            {
                "wakeup_id": record.id,
                "source": record.envelope.source.value,
                "trust": record.envelope.trust.value,
                "status": record.status.value,
                "run_id": record.run_id,
            }
            for record in self.wakeup_store.list()
            if record.status in {WakeupStatus.PENDING, WakeupStatus.CLAIMED}
        ]
        outbox = [
            {
                "outbox_id": record.id,
                "status": record.status.value,
                "channel": record.envelope.channel,
                "conversation_id": record.envelope.conversation_id,
            }
            for record in self.gateway.store.list_outbox()
            if record.status not in {OutboxStatus.SENT, OutboxStatus.REJECTED}
        ]
        turns = []
        for path in sorted(self.runs_root.glob("*.json")):
            state = GatewayTurnStore(path).load()
            if state.status in {TurnStatus.WAITING, TurnStatus.INTERRUPTED}:
                turns.append({
                    "run_id": state.run_id,
                    "wakeup_id": state.wakeup_id,
                    "phase": state.phase.value,
                    "status": state.status.value,
                    "outbox_id": state.outbox_id,
                })
        return {"wakeups": wakeups, "outbox": outbox, "turns": turns}

    def list_turns(self) -> list[GatewayRunResult]:
        states = []
        for path in sorted(self.runs_root.glob("*.json")):
            store = GatewayTurnStore(path)
            state = store.load()
            if state.job_name == self.job.name:
                states.append((state, store))
        states.sort(key=lambda item: (item[0].created_at, item[0].run_id))
        return [self._result(state, store) for state, store in states]

    def _process_claimed(
        self,
        wakeup_record: WakeupRecord,
        *,
        deliver: bool,
    ) -> GatewayRunResult:
        if wakeup_record.claim_token is None:
            raise RuntimeError("claimed wakeup is missing its claim token")
        inbox = self._claim_channel_input(wakeup_record)
        store = self._turn_store(stable_turn_id(wakeup_record.id))
        state = self._load_or_create_state(wakeup_record, inbox, store)
        state.wakeup_claim_token = wakeup_record.claim_token
        state.claim_token = inbox.claim_token if inbox is not None else None
        state.status = TurnStatus.RUNNING
        state.error = ""
        store.save(state)
        try:
            if state.phase == TurnPhase.COMPLETED:
                self._complete_input(state, store)
                return self._result(state, store)
            if state.phase == TurnPhase.RESPOND:
                self._run_response(state, wakeup_record.envelope, store)
            if state.phase == TurnPhase.RESPOND:
                state.status = TurnStatus.BLOCKED
                state.error = "model run ended without response_submit"
                store.save(state)
                self._release_for_retry(state, store)
                return self._result(state, store)
            if state.phase == TurnPhase.DELIVERY:
                self._prepare_delivery(state, store)
                self._complete_input(state, store)
                state.status = TurnStatus.WAITING
                store.save(state)
                if deliver:
                    return self.deliver_turn(state.run_id)
            return self._result(state, store)
        except Exception as error:
            state.status = TurnStatus.INTERRUPTED
            state.error = f"{type(error).__name__}: {error}"
            store.save(state)
            self._release_for_retry(state, store)
            return self._result(state, store)

    def _load_or_create_state(
        self,
        wakeup_record: WakeupRecord,
        inbox: Optional[InboxRecord],
        store: GatewayTurnStore,
    ) -> GatewayTurnState:
        wakeup = wakeup_record.envelope
        conversation = self.conversation_store.resolve(wakeup.route)
        fingerprint = self._message_fingerprint(wakeup)
        if store.exists():
            state = store.load()
            if (
                state.wakeup_id != wakeup.wakeup_id
                or state.message_fingerprint != fingerprint
                or Path(state.workspace).resolve() != self.workspace
            ):
                raise ValueError("persisted NanoOpenClaw turn does not match this wakeup")
            return state
        external_message_id = str(
            wakeup.metadata.get("external_message_id") or wakeup.source_id
        )
        state = GatewayTurnState(
            run_id=stable_turn_id(wakeup.wakeup_id),
            job_name=self.job.name,
            message_fingerprint=fingerprint,
            wakeup_id=wakeup.wakeup_id,
            inbox_id=inbox.id if inbox is not None else None,
            source=wakeup.source,
            trust=wakeup.trust,
            claim_token=inbox.claim_token if inbox is not None else None,
            wakeup_claim_token=wakeup_record.claim_token,
            route=wakeup.route,
            conversation_id=conversation.conversation_id,
            session_id=conversation.session_id,
            external_message_id=external_message_id,
            user_content=wakeup.content,
            workspace=str(self.workspace),
        )
        store.save(state)
        return state

    def _run_response(
        self,
        state: GatewayTurnState,
        wakeup: WakeupEnvelope,
        store: GatewayTurnStore,
    ) -> None:
        assert self._profile is not None
        responses = self._responses_for(wakeup)
        if self.provider_factory is None:
            if not responses:
                raise ValueError(
                    "a provider_factory is required for an unscripted wakeup"
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
            binding.context_service: build_turn_context(conversation, wakeup),
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
                "wakeup_source": wakeup.source.value,
                "wakeup_trust": wakeup.trust.value,
            },
        )
        runtime_spec = self._profile.model_copy(update={"extensions": []})
        build = HarnessBuilder().build(runtime_spec, context=context)
        store.save(state)
        try:
            if build.engine is None:
                raise RuntimeError("NanoOpenClaw profile did not bind NanoEngine")
            report = build.engine.run(
                self._engine_query(wakeup),
                run_id=attempt_id,
                session_id=state.session_id,
            )
            artifact, trace = self.artifacts.save(
                profile=self._profile.name,
                scenario=f"{self.job.name}-{wakeup.source.value}-turn",
                report=report,
            )
            state.artifacts.append(artifact)
            state.total_steps += trace.total_steps
            for name, count in trace.tool_counts.items():
                state.tool_counts[name] = state.tool_counts.get(name, 0) + count
            store.save(state)
        finally:
            build.close()

    def _prepare_delivery(
        self,
        state: GatewayTurnState,
        store: GatewayTurnStore,
    ) -> None:
        envelope = self._outbound_envelope(state)
        if state.outbox_id is None:
            outbox, _ = self.gateway.queue_outbound(
                envelope,
                idempotency_key=f"nano-openclaw:{state.run_id}:response",
            )
            state.outbox_id = outbox.id
            state.delivery_status = outbox.status
            store.save(state)
        elif self.gateway.store.get_outbox(state.outbox_id) is None:
            raise RuntimeError("persisted turn references a missing outbox record")

    def _review_and_deliver(
        self,
        state: GatewayTurnState,
        store: GatewayTurnStore,
    ) -> None:
        envelope = self._outbound_envelope(state)
        assert state.outbox_id is not None
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
                wakeup_id=state.wakeup_id,
                inbox_id=state.inbox_id,
                source=state.source,
                trust=state.trust,
                external_message_id=state.external_message_id,
                user_content=state.user_content,
                assistant_content=state.response,
                delivered=delivered,
                outbox_id=state.outbox_id,
                completed_at=state.completed_at,
            ),
        )
        state.delivery_status = delivery_status
        state.phase = TurnPhase.COMPLETED
        state.status = TurnStatus.COMPLETED
        state.error = ""
        store.save(state)

    def _complete_input(
        self,
        state: GatewayTurnState,
        store: GatewayTurnStore,
    ) -> None:
        if state.inbox_id is not None:
            inbox = self.gateway.store.get_inbox(state.inbox_id)
            if inbox is not None and inbox.status != InboxStatus.COMPLETED:
                if state.claim_token is None:
                    raise RuntimeError("turn lost its channel inbox claim")
                self.gateway.complete_inbox(
                    state.inbox_id,
                    state.claim_token,
                    run_id=state.run_id,
                )
        assert state.wakeup_id is not None
        wakeup = self.wakeup_store.get(state.wakeup_id)
        if wakeup is not None and wakeup.status != WakeupStatus.COMPLETED:
            if state.wakeup_claim_token is None:
                raise RuntimeError("turn lost its wakeup claim")
            self.wakeup_store.complete(
                state.wakeup_id,
                state.wakeup_claim_token,
                run_id=state.run_id,
            )
        state.claim_token = None
        state.wakeup_claim_token = None
        store.save(state)

    def _release_for_retry(
        self,
        state: GatewayTurnState,
        store: GatewayTurnStore,
    ) -> None:
        if state.inbox_id is not None:
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
        if state.wakeup_id is not None:
            wakeup = self.wakeup_store.get(state.wakeup_id)
            if (
                wakeup is not None
                and wakeup.status == WakeupStatus.CLAIMED
                and state.wakeup_claim_token
                and wakeup.claim_token == state.wakeup_claim_token
            ):
                try:
                    self.wakeup_store.fail(
                        state.wakeup_id,
                        state.wakeup_claim_token,
                        error=state.error or "NanoOpenClaw turn requires retry",
                        retryable=True,
                    )
                except Exception:
                    self.wakeup_store.recover_expired()
        remaining_inbox = (
            self.gateway.store.get_inbox(state.inbox_id)
            if state.inbox_id is not None
            else None
        )
        remaining_wakeup = (
            self.wakeup_store.get(state.wakeup_id)
            if state.wakeup_id is not None
            else None
        )
        state.claim_token = (
            remaining_inbox.claim_token
            if remaining_inbox is not None
            and remaining_inbox.status == InboxStatus.CLAIMED
            else None
        )
        state.wakeup_claim_token = (
            remaining_wakeup.claim_token
            if remaining_wakeup is not None
            and remaining_wakeup.status == WakeupStatus.CLAIMED
            else None
        )
        store.save(state)

    def _claim_channel_input(
        self,
        wakeup_record: WakeupRecord,
    ) -> Optional[InboxRecord]:
        wakeup = wakeup_record.envelope
        if wakeup.source != WakeupSource.CHANNEL:
            return None
        assert wakeup.channel_inbox_id is not None
        inbox = self.gateway.store.get_inbox(wakeup.channel_inbox_id)
        if inbox is None:
            raise RuntimeError("channel wakeup references a missing inbox")
        if inbox.status == InboxStatus.RECEIVED:
            return self.gateway.claim_inbox(
                inbox.id,
                f"nano-openclaw:{stable_turn_id(wakeup.wakeup_id)}",
            )
        if inbox.status == InboxStatus.FAILED:
            raise RuntimeError("channel inbox is terminally failed")
        if inbox.status == InboxStatus.COMPLETED:
            return inbox
        store = self._turn_store(stable_turn_id(wakeup.wakeup_id))
        if not store.exists() or store.load().claim_token != inbox.claim_token:
            raise RuntimeError("channel inbox is claimed by a different host attempt")
        return inbox

    def _responses_for(self, wakeup: WakeupEnvelope) -> list[ScriptedResponse]:
        if wakeup.source == WakeupSource.CHANNEL and wakeup.channel_inbox_id:
            inbox = self.gateway.store.get_inbox(wakeup.channel_inbox_id)
            if inbox is not None:
                message = self.job.message_for(inbox.envelope)
                return list(message.responses) if message is not None else []
        script_key = wakeup.metadata.get("script_key")
        if isinstance(script_key, str):
            schedule = self.job.schedule_for(script_key)
            return list(schedule.responses) if schedule is not None else []
        return []

    def _message_fingerprint(self, wakeup: WakeupEnvelope) -> str:
        if wakeup.source == WakeupSource.CHANNEL and wakeup.channel_inbox_id:
            inbox = self.gateway.store.get_inbox(wakeup.channel_inbox_id)
            if inbox is not None:
                message = self.job.message_for(inbox.envelope)
                if message is not None:
                    return message.fingerprint()
        return wakeup.payload_fingerprint

    @staticmethod
    def _engine_query(wakeup: WakeupEnvelope) -> str:
        if wakeup.source == WakeupSource.SCHEDULE:
            return "Handle the trusted scheduled wakeup provided in system context."
        if wakeup.source == WakeupSource.BACKGROUND:
            return "Handle the trusted background completion provided in system context."
        return wakeup.content

    @staticmethod
    def _outbound_envelope(state: GatewayTurnState) -> OutboundEnvelope:
        return OutboundEnvelope(
            channel=state.route.channel,
            account_id=state.route.account_id,
            conversation_id=state.route.conversation_id,
            recipient_id=state.route.sender_id,
            content=state.response,
            reply_to_message_id=(
                state.external_message_id
                if state.source == WakeupSource.CHANNEL
                else None
            ),
            metadata={
                "source": "nano-openclaw",
                "run_id": state.run_id,
                "wakeup_source": state.source.value,
                "wakeup_trust": state.trust.value,
            },
        )

    def _input_completed(self, state: GatewayTurnState) -> bool:
        if state.wakeup_id is None:
            return False
        wakeup = self.wakeup_store.get(state.wakeup_id)
        return wakeup is not None and wakeup.status == WakeupStatus.COMPLETED

    def _sync_channel_inbox(self) -> None:
        for inbox in self.gateway.store.list_inbox():
            if inbox.status != InboxStatus.FAILED:
                self.wakeup_store.ingest(channel_wakeup(inbox))

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
        scheduler = build.context.services.get("scheduler")
        if not isinstance(gateway, DurableChannelGateway):
            build.close()
            raise RuntimeError("NanoOpenClaw profile did not install channels.durable")
        if not isinstance(scheduler, Scheduler):
            build.close()
            raise RuntimeError("NanoOpenClaw profile did not install scheduler.local")
        self._profile = bound
        self._extension_build = build
        self._gateway = gateway
        self._scheduler = scheduler

    def _turn_store(self, run_id: str) -> GatewayTurnStore:
        return GatewayTurnStore(self.runs_root / f"{run_id}.json")

    def _batch(self, results: list[GatewayRunResult]) -> GatewayBatchResult:
        return GatewayBatchResult(
            job=self.job.name,
            success=all(result.success for result in results),
            processed=len(results),
            delivered=sum(
                result.delivery_status == OutboxStatus.SENT for result in results
            ),
            turns=results,
        )

    def _result(
        self,
        state: GatewayTurnState,
        store: GatewayTurnStore,
    ) -> GatewayRunResult:
        assert state.wakeup_id is not None
        return GatewayRunResult(
            job=state.job_name,
            run_id=state.run_id,
            wakeup_id=state.wakeup_id,
            inbox_id=state.inbox_id,
            source=state.source,
            trust=state.trust,
            conversation_id=state.conversation_id,
            session_id=state.session_id,
            status=state.status,
            phase=state.phase,
            success=state.status in {TurnStatus.WAITING, TurnStatus.COMPLETED},
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
