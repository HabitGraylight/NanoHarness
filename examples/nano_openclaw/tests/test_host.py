import json

from app.host import GatewayHost
from app.models import (
    ConversationRoute,
    GatewayJob,
    TurnPhase,
    TurnStatus,
    WakeupSource,
    WakeupStatus,
    WakeupTrust,
)
from nanoharness.core.schema import LLMResponse, ToolCall
from nanoharness.extensions.channels import (
    InboxStatus,
    InboundEnvelope,
    MockChannelAdapter,
    OutboxStatus,
)
from nanoharness.testing import ScriptedLLM, ScriptedResponse


def responses(answer="answer"):
    return [
        ScriptedResponse(
            content="I have the answer.",
            tool_calls=[ToolCall(
                call_id="submit-call",
                name="response_submit",
                arguments={"answer": answer},
            )],
        ),
        ScriptedResponse(content="submitted"),
    ]


def message(
    message_id="message-1",
    *,
    sender_id="user-1",
    content="question",
    scripted=None,
):
    return {
        "envelope": {
            "message_id": message_id,
            "channel": "mock",
            "account_id": "primary",
            "conversation_id": "conversation-1",
            "sender_id": sender_id,
            "content": content,
            "received_at": "2026-08-07T12:00:00Z",
        },
        "responses": [
            item.model_dump(mode="json") for item in (scripted or responses())
        ],
    }


def job(*messages):
    return GatewayJob.model_validate({
        "name": "test-job",
        "messages": list(messages or [message()]),
    })


def scheduled_job():
    payload = job().model_dump(mode="json")
    payload["schedules"] = [{
        "name": "due-check",
        "prompt": "Run trusted check",
        "channel": "mock",
        "account_id": "primary",
        "conversation_id": "conversation-1",
        "sender_id": "user-1",
        "delay_seconds": 0,
        "responses": [item.model_dump(mode="json") for item in responses("due answer")],
    }]
    return GatewayJob.model_validate(payload)


class FailingProvider:
    def chat(self, messages, tools=None):
        raise RuntimeError("provider unavailable")


class CapturingProvider:
    def __init__(self, scripted):
        self.inner = ScriptedLLM(scripted)
        self.messages = []

    def chat(self, messages, tools=None):
        self.messages.append(messages)
        return self.inner.chat(messages, tools)


class FakeNotificationSource:
    def __init__(self, notices):
        self.notices = list(notices)

    def drain(self):
        return list(self.notices)


def test_host_queues_approves_and_delivers_response(tmp_path):
    with GatewayHost(job(), tmp_path) as host:
        result = host.run_job().turns[0]
        outbox = host.gateway.store.get_outbox(result.outbox_id)
        inbox = host.gateway.store.get_inbox(result.inbox_id)

    assert result.success is True
    assert result.phase == TurnPhase.COMPLETED
    assert result.status == TurnStatus.COMPLETED
    assert result.delivery_status == OutboxStatus.SENT
    assert result.approval.approved is True
    assert outbox.status == OutboxStatus.SENT
    assert inbox.status == InboxStatus.COMPLETED


def test_host_rejection_is_terminal_but_not_delivered(tmp_path):
    with GatewayHost(job(), tmp_path, approve_outbound=False) as host:
        result = host.run_job().turns[0]
        outbox = host.gateway.store.get_outbox(result.outbox_id)

    assert result.success is True
    assert result.delivery_status == OutboxStatus.REJECTED
    assert result.approval.approved is False
    assert outbox.status == OutboxStatus.REJECTED
    assert "content" not in result.approval.model_dump()


def test_generate_only_completes_input_and_leaves_durable_pending_outbox(tmp_path):
    with GatewayHost(job(), tmp_path) as host:
        result = host.run_job(deliver=False).turns[0]
        inbox = host.gateway.store.get_inbox(result.inbox_id)
        wakeup = host.wakeup_store.get(result.wakeup_id)
        outbox = host.gateway.store.get_outbox(result.outbox_id)

    assert result.status == TurnStatus.WAITING
    assert result.phase == TurnPhase.DELIVERY
    assert result.success is True
    assert result.approval is None
    assert result.delivery_status == OutboxStatus.PENDING
    assert inbox.status == InboxStatus.COMPLETED
    assert wakeup.status == WakeupStatus.COMPLETED
    assert outbox.status == OutboxStatus.PENDING


def test_pending_delivery_survives_host_restart_without_provider(tmp_path):
    with GatewayHost(job(), tmp_path) as first_host:
        waiting = first_host.run_job(deliver=False).turns[0]

    with GatewayHost(job(), tmp_path) as second_host:
        delivered = second_host.deliver_turn(waiting.run_id)

    assert delivered.status == TurnStatus.COMPLETED
    assert delivered.delivery_status == OutboxStatus.SENT


def test_sending_outbox_recovers_to_approved_after_process_restart(tmp_path):
    with GatewayHost(job(), tmp_path) as first_host:
        waiting = first_host.run_job(deliver=False).turns[0]
        first_host.gateway.approve_outbox(waiting.outbox_id)
        sending = first_host.gateway.store.begin_delivery(waiting.outbox_id)
        assert sending.status == OutboxStatus.SENDING

    with GatewayHost(job(), tmp_path) as second_host:
        recovered = second_host.gateway.store.get_outbox(waiting.outbox_id)
        delivered = second_host.deliver_pending()[0]

    assert recovered.status == OutboxStatus.APPROVED
    assert delivered.delivery_status == OutboxStatus.SENT


def test_completed_wakeup_reconciles_pre_waiting_turn_state(tmp_path):
    with GatewayHost(job(), tmp_path) as host:
        waiting = host.run_job(deliver=False).turns[0]
        store = host._turn_store(waiting.run_id)
        state = store.load()
        state.status = TurnStatus.RUNNING
        store.save(state)

        reconciled = host.process_wakeup(waiting.wakeup_id, deliver=False)

    assert reconciled.status == TurnStatus.WAITING
    assert reconciled.phase == TurnPhase.DELIVERY


def test_sender_route_isolation_creates_distinct_conversations(tmp_path):
    second = message("message-2", sender_id="user-2")
    with GatewayHost(job(message(), second), tmp_path) as host:
        result = host.run_job()

    assert result.processed == 2
    assert result.turns[0].conversation_id != result.turns[1].conversation_id
    assert result.turns[0].session_id != result.turns[1].session_id


def test_same_route_second_turn_receives_delivered_history(tmp_path):
    providers = []

    def factory(_state, scripted):
        provider = CapturingProvider(scripted)
        providers.append(provider)
        return provider

    second = message("message-2", content="follow up")
    with GatewayHost(
        job(message(content="first question"), second),
        tmp_path,
        provider_factory=factory,
    ) as host:
        result = host.run_job()

    first_call = providers[1].messages[0]
    contents = [item["content"] for item in first_call]
    assert result.turns[0].response in contents
    assert "first question" in contents
    assert "follow up" in contents


def test_due_schedule_is_trusted_system_wakeup_without_channel_inbox(tmp_path):
    with GatewayHost(scheduled_job(), tmp_path) as host:
        result = host.run_due(deliver=False)
        turn = result.turns[0]
        wakeup = host.wakeup_store.get(turn.wakeup_id)

    assert result.processed == 1
    assert turn.source == WakeupSource.SCHEDULE
    assert turn.trust == WakeupTrust.TRUSTED_SYSTEM
    assert turn.inbox_id is None
    assert turn.status == TurnStatus.WAITING
    assert wakeup.status == WakeupStatus.COMPLETED


def test_job_schedule_install_and_due_collection_are_idempotent(tmp_path):
    with GatewayHost(scheduled_job(), tmp_path) as host:
        first = host.install_job_schedules()
        second = host.install_job_schedules()
        due = host.collect_due()
        replay = host.collect_due()

    assert first[0]["id"] == second[0]["id"]
    assert len(due) == 1
    assert replay == []


def test_background_notifications_are_deduped_and_processed_as_system(tmp_path):
    notice = {
        "task_id": 4,
        "status": "completed",
        "message": "trusted job completed",
        "exit_code": 0,
        "finished_at": 1786075200.0,
    }
    source = FakeNotificationSource([notice])
    route = ConversationRoute(
        channel="mock",
        account_id="primary",
        conversation_id="conversation-1",
        sender_id="user-1",
    )
    factory = lambda _state, _scripted: ScriptedLLM(responses("background answer"))
    with GatewayHost(job(), tmp_path, provider_factory=factory) as host:
        first = host.collect_notifications(source, route, source_instance="worker-a")
        second = host.collect_notifications(source, route, source_instance="worker-a")
        turn = host.process_wakeup(first[0].id, deliver=False)

    assert first[0].id == second[0].id
    assert turn.source == WakeupSource.BACKGROUND
    assert turn.trust == WakeupTrust.TRUSTED_SYSTEM
    assert turn.inbox_id is None
    assert turn.response == "background answer"


def test_schedule_content_enters_system_context_not_user_role(tmp_path):
    providers = []

    def factory(_state, scripted):
        provider = CapturingProvider(scripted)
        providers.append(provider)
        return provider

    with GatewayHost(scheduled_job(), tmp_path, provider_factory=factory) as host:
        host.run_due(deliver=False)

    messages = providers[0].messages[0]
    assert any(
        item["role"] == "system" and "Run trusted check" in item["content"]
        for item in messages
    )
    assert messages[-1]["role"] == "user"
    assert "trusted scheduled wakeup" in messages[-1]["content"]


def test_manual_wakeup_retains_operator_trust(tmp_path):
    route = ConversationRoute(
        channel="mock",
        account_id="primary",
        conversation_id="manual-conversation",
        sender_id="operator",
    )
    factory = lambda _state, _scripted: ScriptedLLM(responses("manual answer"))
    with GatewayHost(job(), tmp_path, provider_factory=factory) as host:
        record, created = host.ingest_manual(
            "operator request",
            route,
            event_id="manual-1",
        )
        result = host.process_wakeup(record.id, deliver=False)

    assert created is True
    assert result.source == WakeupSource.MANUAL
    assert result.trust == WakeupTrust.OPERATOR


def test_list_pending_separates_waiting_turn_and_outbox(tmp_path):
    with GatewayHost(job(), tmp_path) as host:
        waiting = host.run_job(deliver=False).turns[0]
        inspection = host.list_pending()

    assert inspection["wakeups"] == []
    assert inspection["turns"] == [{
        "run_id": waiting.run_id,
        "wakeup_id": waiting.wakeup_id,
        "phase": "delivery",
        "status": "waiting",
        "outbox_id": waiting.outbox_id,
    }]
    assert inspection["outbox"][0]["status"] == "pending"


def test_missing_response_submit_blocks_and_returns_inbox_to_queue(tmp_path):
    no_submit = [ScriptedResponse(content="forgot the transition")]
    with GatewayHost(job(message(scripted=no_submit)), tmp_path) as host:
        result = host.run_job().turns[0]
        inbox = host.gateway.store.get_inbox(result.inbox_id)

    assert result.success is False
    assert result.status == TurnStatus.BLOCKED
    assert result.phase == TurnPhase.RESPOND
    assert "response_submit" in result.error
    assert inbox.status == InboxStatus.RECEIVED


def test_provider_interruption_can_resume_same_turn(tmp_path):
    calls = 0

    def factory(_state, scripted):
        nonlocal calls
        calls += 1
        return FailingProvider() if calls == 1 else ScriptedLLM(scripted)

    with GatewayHost(job(), tmp_path, provider_factory=factory) as host:
        first = host.run_job().turns[0]
        resumed = host.resume(first.run_id)
        channel = host.gateway.store.snapshot()

    assert first.status == TurnStatus.INTERRUPTED
    assert resumed.success is True
    assert resumed.run_id == first.run_id
    assert resumed.outbox_id is not None
    assert len(channel.inbox) == 1
    assert len(channel.outbox) == 1


def test_failed_delivery_retries_without_rerunning_model(tmp_path):
    provider_calls = 0

    def factory(_state, scripted):
        nonlocal provider_calls
        provider_calls += 1
        return ScriptedLLM(scripted)

    with GatewayHost(job(), tmp_path, provider_factory=factory) as host:
        host.gateway.register_adapter(
            MockChannelAdapter("mock", failures_before_success=1),
            replace=True,
        )
        first = host.run_job().turns[0]
        resumed = host.resume(first.run_id)
        outbox = host.gateway.store.get_outbox(first.outbox_id)

    assert first.status == TurnStatus.INTERRUPTED
    assert first.delivery_status == OutboxStatus.FAILED
    assert resumed.success is True
    assert resumed.delivery_status == OutboxStatus.SENT
    assert provider_calls == 1
    assert len(outbox.attempts) == 2


def test_replaying_completed_job_does_not_duplicate_turn_or_delivery(tmp_path):
    with GatewayHost(job(), tmp_path) as host:
        first = host.run_job().turns[0]
        second = host.run_job().turns[0]
        snapshot = host.gateway.store.snapshot()

    assert first.run_id == second.run_id
    assert first.outbox_id == second.outbox_id
    assert len(snapshot.inbox) == 1
    assert len(snapshot.outbox) == 1


def test_run_pending_processes_external_normalized_envelope(tmp_path):
    def factory(_state, _scripted):
        return ScriptedLLM(responses("external answer"))

    external = InboundEnvelope(
        message_id="external-message",
        channel="mock",
        account_id="primary",
        conversation_id="external-conversation",
        sender_id="external-user",
        content="external question",
    )
    with GatewayHost(job(), tmp_path, provider_factory=factory) as host:
        record, created = host.ingest(external)
        results = host.run_pending(limit=1)

    assert created is True
    assert len(results) == 1
    assert results[0].inbox_id == record.id
    assert results[0].response == "external answer"


def test_run_pending_zero_limit_does_not_claim(tmp_path):
    with GatewayHost(job(), tmp_path) as host:
        record, _ = host.ingest(job().messages[0].envelope)
        assert host.run_pending(limit=0) == []
        inbox = host.gateway.store.get_inbox(record.id)
    assert inbox.status == InboxStatus.RECEIVED


def test_result_exposes_crash_readable_paths(tmp_path):
    with GatewayHost(job(), tmp_path) as host:
        result = host.run_job().turns[0]

    assert json.loads(open(result.state_path, encoding="utf-8").read())["run_id"] == (
        result.run_id
    )
    assert result.conversation_id in json.loads(
        open(result.conversation_path, encoding="utf-8").read()
    )


def test_close_is_idempotent_and_closes_gateway(tmp_path):
    host = GatewayHost(job(), tmp_path)
    gateway = host.gateway
    host.close()
    host.close()
    assert gateway.closed is True
