from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from app.models import (
    ConversationRoute,
    WakeupEnvelope,
    WakeupSource,
    WakeupStatus,
    WakeupTrust,
    stable_wakeup_id,
)
from app.store import WakeupConflictError, WakeupStore, WakeupTransitionError
from app.wakeups import background_wakeup, manual_wakeup, schedule_wakeup


def route():
    return ConversationRoute(
        channel="mock",
        account_id="primary",
        conversation_id="conversation-1",
        sender_id="user-1",
    )


def manual(event_id="one", content="operator request"):
    return manual_wakeup(content, route(), event_id=event_id)


def test_source_identity_assigns_stable_ids_and_fixed_trust():
    wakeup = manual()
    assert wakeup.wakeup_id == stable_wakeup_id(
        WakeupSource.MANUAL,
        "operator:one",
    )
    assert wakeup.trust == WakeupTrust.OPERATOR


def test_source_cannot_escalate_its_declared_trust():
    wakeup = manual().model_dump(mode="json")
    wakeup["trust"] = "trusted_system"
    with pytest.raises(ValidationError, match="operator"):
        WakeupEnvelope.model_validate(wakeup)


def test_schedule_notification_becomes_trusted_system_wakeup():
    wakeup = schedule_wakeup({
        "schedule_id": 7,
        "fire_count": 2,
        "prompt": "Check status",
        "status": "active",
        "fired_at": 1786075200.0,
        "metadata": {
            "route": route().model_dump(mode="json"),
            "script_key": "check",
        },
    })

    assert wakeup.source == WakeupSource.SCHEDULE
    assert wakeup.trust == WakeupTrust.TRUSTED_SYSTEM
    assert wakeup.source_id == "schedule:7:2"
    assert wakeup.metadata["script_key"] == "check"


def test_schedule_notification_requires_structured_route():
    with pytest.raises(ValueError, match="route"):
        schedule_wakeup({
            "schedule_id": 1,
            "fire_count": 1,
            "prompt": "Check",
            "metadata": {},
        })


def test_background_identity_includes_source_instance_and_completion():
    notice = {
        "task_id": 3,
        "status": "completed",
        "message": "job done",
        "exit_code": 0,
        "finished_at": 1786075200.0,
    }
    first = background_wakeup(notice, route(), source_instance="worker-a")
    second = background_wakeup(notice, route(), source_instance="worker-a")
    other = background_wakeup(notice, route(), source_instance="worker-b")

    assert first == second
    assert first.wakeup_id != other.wakeup_id
    assert first.source == WakeupSource.BACKGROUND
    assert first.trust == WakeupTrust.TRUSTED_SYSTEM


def test_wakeup_store_dedupes_stable_event(tmp_path):
    store = WakeupStore(tmp_path / "wakeups.json")
    first, created = store.ingest(manual())
    replay, replay_created = store.ingest(manual())

    assert created is True
    assert replay_created is False
    assert replay.id == first.id
    assert len(store.list()) == 1


def test_wakeup_store_rejects_identity_payload_conflict(tmp_path):
    store = WakeupStore(tmp_path / "wakeups.json")
    store.ingest(manual(content="first"))
    with pytest.raises(WakeupConflictError, match="different payload"):
        store.ingest(manual(content="changed"))


def test_wakeup_store_claim_complete_and_terminal_reclaim(tmp_path):
    store = WakeupStore(tmp_path / "wakeups.json")
    record, _ = store.ingest(manual())
    claimed = store.claim(record.id, "worker")
    completed = store.complete(
        record.id,
        claimed.claim_token,
        run_id="run-1",
    )

    assert completed.status == WakeupStatus.COMPLETED
    assert completed.run_id == "run-1"
    with pytest.raises(WakeupTransitionError, match="not pending"):
        store.claim(record.id, "again")


def test_wakeup_store_retry_returns_event_to_pending(tmp_path):
    store = WakeupStore(tmp_path / "wakeups.json")
    record, _ = store.ingest(manual())
    claimed = store.claim(record.id, "worker")
    failed = store.fail(
        record.id,
        claimed.claim_token,
        error="provider down",
        retryable=True,
    )

    assert failed.status == WakeupStatus.PENDING
    assert failed.last_error == "provider down"
    assert store.claim_next("retry").id == record.id


def test_wakeup_store_recovers_expired_claim(tmp_path):
    start = datetime(2026, 8, 13, 12, 0, tzinfo=timezone.utc)
    store = WakeupStore(tmp_path / "wakeups.json")
    record, _ = store.ingest(manual())
    first = store.claim(record.id, "worker", lease_seconds=1, now=start)

    resumed = store.claim(
        record.id,
        "resume",
        now=start + timedelta(seconds=2),
    )

    assert resumed.claim_count == 2
    assert resumed.claim_token != first.claim_token


def test_wakeup_store_can_recover_expired_claims_without_claiming(tmp_path):
    start = datetime(2026, 8, 13, 12, 0, tzinfo=timezone.utc)
    store = WakeupStore(tmp_path / "wakeups.json")
    record, _ = store.ingest(manual())
    store.claim(record.id, "worker", lease_seconds=1, now=start)

    assert store.recover_expired(now=start + timedelta(seconds=2)) == 1
    assert store.get(record.id).status == WakeupStatus.PENDING


def test_wakeup_store_rejects_stale_token(tmp_path):
    store = WakeupStore(tmp_path / "wakeups.json")
    record, _ = store.ingest(manual())
    store.claim(record.id, "worker")
    with pytest.raises(WakeupTransitionError, match="token"):
        store.complete(record.id, "wrong", run_id="run-1")
