import json

import pytest

from app.models import (
    ConversationExchange,
    ConversationRoute,
    GatewayTurnState,
    stable_turn_id,
)
from app.store import (
    ConversationConflictError,
    ConversationStore,
    GatewayTurnStore,
)
from nanoharness.extensions.channels import InboundEnvelope


def route(sender_id="user-1"):
    return ConversationRoute.from_envelope(InboundEnvelope(
        message_id="message-1",
        channel="mock",
        account_id="primary",
        conversation_id="conversation-1",
        sender_id=sender_id,
        content="hello",
    ))


def exchange(answer="answer", delivered=True):
    return ConversationExchange(
        turn_id="turn-1",
        inbox_id="in-1",
        external_message_id="message-1",
        user_content="question",
        assistant_content=answer,
        delivered=delivered,
        outbox_id="out-1",
        completed_at="2026-08-07T12:00:00Z",
    )


def test_resolve_persists_and_reuses_route(tmp_path):
    store = ConversationStore(tmp_path / "conversations.json")

    first = store.resolve(route())
    second = store.resolve(route())

    assert first == second
    assert len(json.loads(store.path.read_text())) == 1


def test_resolve_isolates_distinct_sender_routes(tmp_path):
    store = ConversationStore(tmp_path / "conversations.json")
    first = store.resolve(route("user-1"))
    second = store.resolve(route("user-2"))

    assert first.conversation_id != second.conversation_id
    assert len(json.loads(store.path.read_text())) == 2


def test_commit_appends_exchange_and_returns_defensive_copy(tmp_path):
    store = ConversationStore(tmp_path / "conversations.json")
    conversation = store.resolve(route())
    committed = store.commit(conversation.conversation_id, exchange())
    committed.exchanges.clear()

    assert len(store.get(conversation.conversation_id).exchanges) == 1


def test_commit_is_idempotent_even_if_retry_timestamp_differs(tmp_path):
    store = ConversationStore(tmp_path / "conversations.json")
    conversation = store.resolve(route())
    store.commit(conversation.conversation_id, exchange())
    retry = exchange().model_copy(update={"completed_at": "2026-08-07T13:00:00Z"})

    state = store.commit(conversation.conversation_id, retry)

    assert len(state.exchanges) == 1


def test_commit_rejects_changed_payload_for_same_turn(tmp_path):
    store = ConversationStore(tmp_path / "conversations.json")
    conversation = store.resolve(route())
    store.commit(conversation.conversation_id, exchange())

    with pytest.raises(ConversationConflictError, match="different exchange"):
        store.commit(conversation.conversation_id, exchange(answer="changed"))


def test_commit_requires_existing_conversation(tmp_path):
    store = ConversationStore(tmp_path / "conversations.json")
    with pytest.raises(KeyError, match="not found"):
        store.commit("missing", exchange())


def test_turn_store_round_trips_strict_state(tmp_path):
    route_value = route()
    state = GatewayTurnState(
        run_id=stable_turn_id("in-1"),
        job_name="job",
        message_fingerprint="fingerprint",
        inbox_id="in-1",
        route=route_value,
        conversation_id=route_value.stable_conversation_id,
        session_id=route_value.stable_session_id,
        external_message_id="message-1",
        user_content="hello",
        workspace=str(tmp_path),
    )
    store = GatewayTurnStore(tmp_path / "runs" / f"{state.run_id}.json")

    store.save(state)

    assert store.exists()
    assert store.load() == state
    assert not store.path.with_suffix(".json.tmp").exists()


def test_store_rejects_corrupt_json(tmp_path):
    store = ConversationStore(tmp_path / "conversations.json")
    store.path.write_text("[]")
    with pytest.raises(ValueError, match="object"):
        store.resolve(route())
