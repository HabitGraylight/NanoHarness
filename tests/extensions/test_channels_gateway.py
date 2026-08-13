import pytest

from nanoharness.components.tools import DictToolRegistry
from nanoharness.extensions.channels import (
    ChannelConflictError,
    ChannelDeliveryError,
    ChannelStateTransitionError,
    DurableChannelGateway,
    DurableChannelStore,
    InboxStatus,
    MockChannelAdapter,
    OutboundEnvelope,
    OutboxStatus,
    register_channel_tools,
)


def inbound(message_id="message-1", content="Hello"):
    return {
        "message_id": message_id,
        "channel": "mock",
        "account_id": "primary",
        "conversation_id": "conversation-1",
        "sender_id": "user-1",
        "content": content,
    }


def outbound(content="Reply", channel="mock"):
    return {
        "channel": channel,
        "account_id": "primary",
        "conversation_id": "conversation-1",
        "recipient_id": "user-1",
        "content": content,
    }


@pytest.fixture
def gateway(tmp_path):
    adapter = MockChannelAdapter("mock")
    service = DurableChannelGateway(
        DurableChannelStore(str(tmp_path / "state.json")),
        [adapter],
    )
    return service, adapter


def test_gateway_accepts_mapping_ingress_and_completes_claim(gateway):
    service, _ = gateway
    record, created = service.ingest(inbound())
    replay, replay_created = service.ingest(inbound())
    assert created is True
    assert replay_created is False
    assert replay.id == record.id

    claimed = service.claim_next("host-1")
    completed = service.complete_inbox(
        claimed.id,
        claimed.claim_token,
        run_id="run-1",
    )
    assert completed.status == InboxStatus.COMPLETED


def test_gateway_failed_ingress_can_return_to_queue(gateway):
    service, _ = gateway
    service.ingest(inbound())
    claimed = service.claim_next("host-1")
    retry = service.fail_inbox(
        claimed.id,
        claimed.claim_token,
        error="provider unavailable",
        retryable=True,
    )
    assert retry.status == InboxStatus.RECEIVED
    assert service.claim_next("host-2").id == claimed.id


def test_gateway_can_claim_a_specific_inbox_for_resume(gateway):
    service, _ = gateway
    first, _ = service.ingest(inbound("message-1"))
    second, _ = service.ingest(inbound("message-2"))

    claimed = service.claim_inbox(second.id, "resume-host")

    assert claimed.id == second.id
    assert service.store.get_inbox(first.id).status == InboxStatus.RECEIVED


def test_gateway_delivers_only_after_explicit_approval(gateway):
    service, adapter = gateway
    queued, _ = service.queue_outbound(
        outbound(),
        idempotency_key="run-1:reply",
    )
    with pytest.raises(ChannelStateTransitionError, match="not approved"):
        service.deliver(queued.id)
    assert adapter.deliveries == []

    service.approve_outbox(queued.id)
    sent = service.deliver(queued.id)
    assert sent.status == OutboxStatus.SENT
    assert len(adapter.deliveries) == 1
    assert sent.external_delivery_id == adapter.deliveries[0].external_delivery_id


def test_gateway_persists_missing_adapter_failure(tmp_path):
    service = DurableChannelGateway(
        DurableChannelStore(str(tmp_path / "state.json"))
    )
    queued, _ = service.queue_outbound(
        outbound(channel="webhook"),
        idempotency_key="missing-adapter",
    )
    service.approve_outbox(queued.id)
    failed = service.deliver(queued.id)
    assert failed.status == OutboxStatus.FAILED
    assert "no adapter registered" in failed.last_error


def test_mock_adapter_failure_then_retry_is_durable(tmp_path):
    adapter = MockChannelAdapter("mock", failures_before_success=1)
    service = DurableChannelGateway(
        DurableChannelStore(str(tmp_path / "state.json")),
        [adapter],
    )
    queued, _ = service.queue_outbound(outbound(), idempotency_key="retry-key")
    service.approve_outbox(queued.id)
    first = service.deliver(queued.id)
    assert first.status == OutboxStatus.FAILED
    assert len(first.attempts) == 1

    service.retry_outbox(queued.id)
    second = service.deliver(queued.id)
    assert second.status == OutboxStatus.SENT
    assert len(second.attempts) == 2
    assert adapter.attempt_count("retry-key") == 2


def test_mock_adapter_reuses_receipt_for_same_idempotency_key():
    adapter = MockChannelAdapter("mock")
    message = OutboundEnvelope.model_validate(outbound())
    first = adapter.send(message, idempotency_key="same-key")
    second = adapter.send(message, idempotency_key="same-key")
    assert second == first
    assert len(adapter.deliveries) == 1
    assert adapter.attempt_count("same-key") == 1


def test_mock_adapter_rejects_wrong_channel_and_closed_state():
    adapter = MockChannelAdapter("mock")
    with pytest.raises(ChannelDeliveryError, match="cannot deliver"):
        adapter.send(
            OutboundEnvelope.model_validate(outbound(channel="console")),
            idempotency_key="wrong",
        )
    adapter.close()
    with pytest.raises(ChannelDeliveryError, match="closed"):
        adapter.send(
            OutboundEnvelope.model_validate(outbound()),
            idempotency_key="closed",
        )


def test_deliver_pending_respects_status_and_limit(gateway):
    service, adapter = gateway
    ids = []
    for index in range(3):
        record, _ = service.queue_outbound(
            outbound(f"Reply {index}"),
            idempotency_key=f"key-{index}",
        )
        ids.append(record.id)
    service.approve_outbox(ids[0])
    service.approve_outbox(ids[1])
    sent = service.deliver_pending(limit=1)
    assert [record.id for record in sent] == [ids[0]]
    assert len(adapter.deliveries) == 1
    assert service.store.get_outbox(ids[1]).status == OutboxStatus.APPROVED
    assert service.store.get_outbox(ids[2]).status == OutboxStatus.PENDING
    with pytest.raises(ValueError, match="non-negative"):
        service.deliver_pending(limit=-1)


def test_gateway_recovers_interrupted_delivery_on_restart(tmp_path):
    path = tmp_path / "state.json"
    store = DurableChannelStore(str(path))
    queued, _ = store.queue_outbound(
        OutboundEnvelope.model_validate(outbound()),
        idempotency_key="recovery-key",
    )
    store.approve_outbox(queued.id)
    store.begin_delivery(queued.id)

    service = DurableChannelGateway(
        DurableChannelStore(str(path)),
        [MockChannelAdapter("mock")],
    )
    recovered = service.store.get_outbox(queued.id)
    assert recovered.status == OutboxStatus.APPROVED
    assert service.deliver(queued.id).status == OutboxStatus.SENT


def test_recovery_resends_with_same_key_after_adapter_succeeded(tmp_path):
    path = tmp_path / "state.json"
    adapter = MockChannelAdapter("mock")
    store = DurableChannelStore(str(path))
    queued, _ = store.queue_outbound(
        OutboundEnvelope.model_validate(outbound()),
        idempotency_key="stable-after-send",
    )
    store.approve_outbox(queued.id)
    sending = store.begin_delivery(queued.id)
    adapter.send(
        sending.envelope,
        idempotency_key=sending.idempotency_key,
    )

    service = DurableChannelGateway(
        DurableChannelStore(str(path)),
        [adapter],
    )
    sent = service.deliver(queued.id)
    assert sent.status == OutboxStatus.SENT
    assert len(adapter.deliveries) == 1
    assert adapter.attempt_count("stable-after-send") == 1


def test_adapter_registration_conflict_and_replace_closes_previous(tmp_path):
    first = MockChannelAdapter("mock")
    service = DurableChannelGateway(
        DurableChannelStore(str(tmp_path / "state.json")),
        [first],
    )
    with pytest.raises(ValueError, match="already registered"):
        service.register_adapter(MockChannelAdapter("mock"))
    second = MockChannelAdapter("mock")
    service.register_adapter(second, replace=True)
    assert first.closed is True
    assert service.channels == ["mock"]


def test_adapter_names_are_validated(tmp_path):
    with pytest.raises(ValueError, match="channel name"):
        MockChannelAdapter("bad/channel")

    class InvalidAdapter:
        channel = "bad/channel"

        def send(self, envelope, *, idempotency_key):
            raise AssertionError("not called")

        def close(self):
            pass

    service = DurableChannelGateway(
        DurableChannelStore(str(tmp_path / "state.json"))
    )
    with pytest.raises(ValueError, match="adapter name"):
        service.register_adapter(InvalidAdapter())


def test_gateway_close_is_idempotent_and_blocks_new_work(gateway):
    service, adapter = gateway
    service.close()
    service.close()
    assert service.closed is True
    assert adapter.closed is True
    with pytest.raises(RuntimeError, match="closed"):
        service.ingest(inbound())


def test_channel_send_tool_queues_without_delivering(gateway):
    service, adapter = gateway
    registry = DictToolRegistry()
    assert register_channel_tools(
        registry,
        service,
        idempotency_scope="run-1",
    ) == ["channel_send"]
    result = registry.call("channel_send", {
        "message_key": "primary-answer",
        **outbound(),
    })
    assert "Queued outbound message" in result
    assert len(service.store.list_outbox()) == 1
    assert service.store.list_outbox()[0].status == OutboxStatus.PENDING
    assert adapter.deliveries == []


def test_channel_send_tool_reuses_key_and_rejects_payload_change(gateway):
    service, _ = gateway
    registry = DictToolRegistry()
    register_channel_tools(registry, service, idempotency_scope="run-1")
    args = {"message_key": "answer", **outbound()}
    registry.call("channel_send", args)
    assert "Reused outbound message" in registry.call("channel_send", args)
    with pytest.raises(ChannelConflictError, match="different payload"):
        registry.call(
            "channel_send",
            {"message_key": "answer", **outbound("Changed")},
        )


def test_channel_send_tool_scope_separates_runs(gateway):
    service, _ = gateway
    first = DictToolRegistry()
    second = DictToolRegistry()
    register_channel_tools(first, service, idempotency_scope="run-1")
    register_channel_tools(second, service, idempotency_scope="run-2")
    args = {"message_key": "answer", **outbound()}
    first.call("channel_send", args)
    second.call("channel_send", args)
    assert len(service.store.list_outbox()) == 2


def test_channel_send_tool_schema_requires_stable_message_key(gateway):
    service, _ = gateway
    registry = DictToolRegistry()
    register_channel_tools(
        registry,
        service,
        idempotency_scope="run-1",
        tool_prefix="gateway_",
    )
    schema = registry.get_tool_schemas()[0]["function"]
    assert schema["name"] == "gateway_channel_send"
    assert "message_key" in schema["parameters"]["required"]
    with pytest.raises(ValueError):
        registry.call(
            "gateway_channel_send",
            {"message_key": "", **outbound()},
        )
