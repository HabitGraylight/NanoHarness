import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest

from nanoharness.extensions.channels import (
    ChannelConflictError,
    ChannelStateTransitionError,
    ChannelStoreError,
    DeliveryAttemptStatus,
    DeliveryReceipt,
    DurableChannelStore,
    InboxStatus,
    InboundEnvelope,
    OutboundEnvelope,
    OutboxStatus,
)


def inbound(message_id="message-1", content="Hello", **updates):
    values = {
        "message_id": message_id,
        "channel": "mock",
        "account_id": "primary",
        "conversation_id": "conversation-1",
        "sender_id": "user-1",
        "content": content,
    }
    values.update(updates)
    return InboundEnvelope.model_validate(values)


def outbound(content="Reply", **updates):
    values = {
        "channel": "mock",
        "account_id": "primary",
        "conversation_id": "conversation-1",
        "recipient_id": "user-1",
        "content": content,
    }
    values.update(updates)
    return OutboundEnvelope.model_validate(values)


@pytest.fixture
def store(tmp_path):
    return DurableChannelStore(str(tmp_path / "channels" / "state.json"))


def test_store_creates_parent_and_persists_inbox(store):
    record, created = store.ingest(inbound())
    assert created is True
    assert store.path.exists()

    loaded = DurableChannelStore(str(store.path))
    assert loaded.get_inbox(record.id) == record


def test_ingress_replay_returns_existing_record(store):
    first, created = store.ingest(inbound(received_at="2026-08-07T12:00:00Z"))
    replay, replay_created = store.ingest(
        inbound(received_at="2026-08-07T12:01:00Z")
    )
    assert created is True
    assert replay_created is False
    assert replay == first
    assert len(store.list_inbox()) == 1


def test_ingress_identity_reuse_with_changed_payload_is_rejected(store):
    store.ingest(inbound(content="Original"))
    with pytest.raises(ChannelConflictError, match="different payload"):
        store.ingest(inbound(content="Replacement"))


def test_concurrent_ingress_dedupes_to_one_record(store):
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: store.ingest(inbound()), range(32)))
    assert sum(created for _, created in results) == 1
    assert len({record.id for record, _ in results}) == 1
    assert len(store.list_inbox()) == 1


def test_claim_next_uses_receive_order_and_returns_lease(store):
    later, _ = store.ingest(
        inbound("later", received_at="2026-08-07T12:02:00Z")
    )
    earlier, _ = store.ingest(
        inbound("earlier", received_at="2026-08-07T12:01:00Z")
    )
    claimed = store.claim_next("worker-1")
    assert claimed is not None
    assert claimed.id == earlier.id
    assert claimed.id != later.id
    assert claimed.status == InboxStatus.CLAIMED
    assert claimed.claim_owner == "worker-1"
    assert claimed.claim_token
    assert claimed.lease_expires_at > claimed.claimed_at


def test_claim_next_is_exclusive_under_concurrency(store):
    store.ingest(inbound())
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda index: store.claim_next(f"w-{index}"), range(8)))
    claimed = [record for record in results if record is not None]
    assert len(claimed) == 1


def test_complete_inbox_requires_active_token_and_records_run(store):
    record, _ = store.ingest(inbound())
    claimed = store.claim_next("worker")
    with pytest.raises(ChannelStateTransitionError, match="token"):
        store.complete_inbox(record.id, "wrong", run_id="run-1")
    completed = store.complete_inbox(
        record.id,
        claimed.claim_token,
        run_id="run-1",
    )
    assert completed.status == InboxStatus.COMPLETED
    assert completed.run_id == "run-1"
    assert completed.claim_token is None


def test_completed_inbox_cannot_be_claimed_again(store):
    record, _ = store.ingest(inbound())
    claimed = store.claim_next("worker")
    store.complete_inbox(record.id, claimed.claim_token, run_id="run-1")
    assert store.claim_next("worker") is None


def test_expired_claim_is_recovered_and_stale_ack_is_rejected(store):
    start = datetime(2026, 8, 7, 12, 0, tzinfo=timezone.utc)
    record, _ = store.ingest(inbound())
    first = store.claim_next("worker-1", lease_seconds=1, now=start)
    with pytest.raises(ChannelStateTransitionError, match="expired"):
        store.complete_inbox(
            record.id,
            first.claim_token,
            run_id="stale-run",
            now=start + timedelta(seconds=2),
        )
    assert store.recover_expired_claims(now=start + timedelta(seconds=2)) == 1
    second = store.claim_next("worker-2", now=start + timedelta(seconds=2))
    assert second.id == record.id
    assert second.claim_token != first.claim_token
    assert second.claim_count == 2


@pytest.mark.parametrize(
    "retryable,expected",
    [(True, InboxStatus.RECEIVED), (False, InboxStatus.FAILED)],
)
def test_fail_inbox_has_explicit_retry_semantics(store, retryable, expected):
    record, _ = store.ingest(inbound())
    claimed = store.claim_next("worker")
    failed = store.fail_inbox(
        record.id,
        claimed.claim_token,
        error="provider failed",
        retryable=retryable,
    )
    assert failed.status == expected
    assert failed.last_error == "provider failed"
    assert failed.claim_token is None


def test_inbox_filter_and_snapshot_are_defensive_copies(store):
    first, _ = store.ingest(inbound("one"))
    store.ingest(inbound("two"))
    claimed = store.claim_next("worker")
    store.complete_inbox(claimed.id, claimed.claim_token, run_id="run-1")
    assert [record.id for record in store.list_inbox("completed")] == [first.id]

    snapshot = store.snapshot()
    snapshot.inbox.clear()
    assert len(store.list_inbox()) == 2


def test_event_audit_omits_message_content(store):
    record, _ = store.ingest(inbound(content="TOP SECRET CONTENT"))
    claimed = store.claim_next("worker")
    store.complete_inbox(record.id, claimed.claim_token, run_id="run-1")
    events = store.events_path.read_text(encoding="utf-8")
    assert "TOP SECRET CONTENT" not in events
    assert "inbox.received" in events
    assert "inbox.completed" in events


def test_invalid_state_is_not_silently_replaced(tmp_path):
    path = tmp_path / "state.json"
    path.write_text("{not-json", encoding="utf-8")
    with pytest.raises(ChannelStoreError, match="invalid durable channel state"):
        DurableChannelStore(str(path))
    assert path.read_text(encoding="utf-8") == "{not-json"


def test_tampered_state_identity_is_rejected_on_reload(store):
    record, _ = store.ingest(inbound())
    payload = json.loads(store.path.read_text(encoding="utf-8"))
    payload["inbox"][record.id]["payload_fingerprint"] = "0" * 64
    store.path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ChannelStoreError, match="invalid durable channel state"):
        DurableChannelStore(str(store.path))


def test_store_rejects_directory_paths_and_overlapping_event_path(tmp_path):
    directory = tmp_path / "directory"
    directory.mkdir()
    with pytest.raises(ValueError, match="must be a file"):
        DurableChannelStore(str(directory))
    with pytest.raises(ValueError, match="must be different"):
        DurableChannelStore(str(tmp_path / "state.json"), events_path=str(tmp_path / "state.json"))


def test_queue_outbound_is_persistent_and_idempotent(store):
    record, created = store.queue_outbound(outbound(), idempotency_key="run-1:reply")
    duplicate, duplicate_created = store.queue_outbound(
        outbound(),
        idempotency_key="run-1:reply",
    )
    assert created is True
    assert duplicate_created is False
    assert duplicate == record
    assert DurableChannelStore(str(store.path)).get_outbox(record.id) == record


def test_outbound_key_reuse_with_changed_payload_is_rejected(store):
    store.queue_outbound(outbound("Original"), idempotency_key="same-key")
    with pytest.raises(ChannelConflictError, match="different payload"):
        store.queue_outbound(outbound("Changed"), idempotency_key="same-key")


def test_outbound_approval_and_rejection_are_separate_terminal_choices(store):
    approved, _ = store.queue_outbound(outbound("Approved"), idempotency_key="a")
    rejected, _ = store.queue_outbound(outbound("Rejected"), idempotency_key="b")
    assert store.approve_outbox(approved.id).status == OutboxStatus.APPROVED
    assert store.approve_outbox(approved.id).status == OutboxStatus.APPROVED
    denied = store.reject_outbox(rejected.id, reason="not allowed")
    assert denied.status == OutboxStatus.REJECTED
    assert denied.last_error == "not allowed"
    with pytest.raises(ChannelStateTransitionError):
        store.reject_outbox(approved.id)


def test_begin_delivery_creates_tokenized_attempt(store):
    queued, _ = store.queue_outbound(outbound(), idempotency_key="key")
    store.approve_outbox(queued.id)
    sending = store.begin_delivery(queued.id)
    assert sending.status == OutboxStatus.SENDING
    assert sending.delivery_token
    assert len(sending.attempts) == 1
    assert sending.attempts[0].number == 1
    assert sending.attempts[0].status == DeliveryAttemptStatus.SENDING


def test_mark_sent_requires_matching_delivery_token_and_receipt(store):
    queued, _ = store.queue_outbound(outbound(), idempotency_key="key")
    store.approve_outbox(queued.id)
    sending = store.begin_delivery(queued.id)
    receipt = DeliveryReceipt(
        channel="mock",
        idempotency_key="key",
        external_delivery_id="external-1",
    )
    with pytest.raises(ChannelStateTransitionError, match="token"):
        store.mark_delivery_sent(queued.id, "wrong", receipt)
    with pytest.raises(ChannelConflictError, match="idempotency"):
        store.mark_delivery_sent(
            queued.id,
            sending.delivery_token,
            receipt.model_copy(update={"idempotency_key": "wrong"}),
        )
    sent = store.mark_delivery_sent(queued.id, sending.delivery_token, receipt)
    assert sent.status == OutboxStatus.SENT
    assert sent.external_delivery_id == "external-1"
    assert sent.attempts[0].status == DeliveryAttemptStatus.SENT


def test_failed_delivery_can_be_reapproved_for_a_new_attempt(store):
    queued, _ = store.queue_outbound(outbound(), idempotency_key="key")
    store.approve_outbox(queued.id)
    first = store.begin_delivery(queued.id)
    failed = store.mark_delivery_failed(
        queued.id,
        first.delivery_token,
        error="network down",
    )
    assert failed.status == OutboxStatus.FAILED
    assert failed.attempts[0].status == DeliveryAttemptStatus.FAILED
    assert store.retry_outbox(queued.id).status == OutboxStatus.APPROVED
    second = store.begin_delivery(queued.id)
    assert len(second.attempts) == 2
    assert second.attempts[-1].token != first.delivery_token


def test_recover_sending_reuses_outbox_idempotency_key(store):
    queued, _ = store.queue_outbound(outbound(), idempotency_key="stable-key")
    store.approve_outbox(queued.id)
    store.begin_delivery(queued.id)
    assert store.recover_sending() == 1
    recovered = store.get_outbox(queued.id)
    assert recovered.status == OutboxStatus.APPROVED
    assert recovered.idempotency_key == "stable-key"
    assert recovered.delivery_token is None
    assert recovered.attempts[-1].status == DeliveryAttemptStatus.RECOVERED
    assert store.recover_sending() == 0


def test_outbox_filter_returns_creation_order(store):
    first, _ = store.queue_outbound(outbound("First"), idempotency_key="first")
    second, _ = store.queue_outbound(outbound("Second"), idempotency_key="second")
    store.approve_outbox(second.id)
    assert [record.id for record in store.list_outbox()] == [first.id, second.id]
    assert [record.id for record in store.list_outbox("approved")] == [second.id]


def test_store_state_is_valid_json_without_temporary_residue(store):
    store.ingest(inbound())
    payload = json.loads(store.path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "1.0"
    assert not store.path.with_name(store.path.name + ".tmp").exists()
