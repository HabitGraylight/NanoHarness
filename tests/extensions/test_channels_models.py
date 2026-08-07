from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from nanoharness.extensions.channels import (
    ChannelStoreState,
    InboxRecord,
    InboundEnvelope,
    OutboundEnvelope,
    OutboxRecord,
    stable_inbox_id,
    stable_outbox_id,
    stable_tool_idempotency_key,
)


def inbound(**updates):
    values = {
        "message_id": "message-1",
        "channel": "mock",
        "account_id": "primary",
        "conversation_id": "conversation-1",
        "sender_id": "user-1",
        "content": "Hello gateway",
    }
    values.update(updates)
    return InboundEnvelope.model_validate(values)


def outbound(**updates):
    values = {
        "channel": "mock",
        "account_id": "primary",
        "conversation_id": "conversation-1",
        "recipient_id": "user-1",
        "content": "Hello user",
    }
    values.update(updates)
    return OutboundEnvelope.model_validate(values)


def test_inbound_envelope_has_no_role_injection_surface():
    with pytest.raises(ValidationError, match="role"):
        inbound(role="system")


@pytest.mark.parametrize("content", ["", " ", "\n\t"])
def test_inbound_rejects_blank_content(content):
    with pytest.raises(ValidationError, match="blank"):
        inbound(content=content)


@pytest.mark.parametrize("channel", ["", "-bad", "bad/channel", "x" * 65])
def test_inbound_rejects_invalid_channel(channel):
    with pytest.raises(ValidationError):
        inbound(channel=channel)


@pytest.mark.parametrize(
    "field,value",
    [
        ("message_id", " padded "),
        ("account_id", "bad\nvalue"),
        ("conversation_id", ""),
        ("sender_id", "x" * 257),
    ],
)
def test_inbound_rejects_unsafe_external_identifiers(field, value):
    with pytest.raises(ValidationError):
        inbound(**{field: value})


def test_inbound_requires_timezone_aware_timestamp():
    with pytest.raises(ValidationError, match="timezone"):
        inbound(received_at=datetime(2026, 8, 7, 12, 0, 0))


def test_inbound_normalizes_timestamp_to_utc():
    value = inbound(received_at="2026-08-07T20:00:00+08:00")
    assert value.received_at == datetime(2026, 8, 7, 12, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize("metadata", [{"bad": float("nan")}, {"bad": object()}])
def test_inbound_metadata_must_be_finite_json(metadata):
    with pytest.raises(ValidationError, match="finite JSON"):
        inbound(metadata=metadata)


def test_inbound_dedupe_identity_excludes_receive_time():
    first = inbound(received_at="2026-08-07T12:00:00Z")
    replay = inbound(received_at="2026-08-07T12:01:00Z")
    assert first.dedupe_key == '["mock","primary","message-1"]'
    assert first.payload_fingerprint == replay.payload_fingerprint
    assert stable_inbox_id(first) == stable_inbox_id(replay)


def test_inbound_dedupe_identity_is_unambiguous_when_ids_contain_colons():
    first = inbound(account_id="a:b", message_id="c")
    second = inbound(account_id="a", message_id="b:c")
    assert first.dedupe_key != second.dedupe_key
    assert stable_inbox_id(first) != stable_inbox_id(second)


def test_inbound_payload_fingerprint_covers_content_and_route():
    original = inbound()
    assert original.payload_fingerprint != inbound(content="Changed").payload_fingerprint
    assert original.payload_fingerprint != inbound(sender_id="user-2").payload_fingerprint
    assert original.payload_fingerprint != inbound(metadata={"x": 1}).payload_fingerprint


def test_outbound_envelope_is_strict_and_rejects_blank_content():
    with pytest.raises(ValidationError, match="role"):
        outbound(role="assistant")
    with pytest.raises(ValidationError, match="blank"):
        outbound(content="  ")


def test_stable_outbox_id_is_key_based():
    assert stable_outbox_id("run-1:message-1") == stable_outbox_id(
        "run-1:message-1"
    )
    assert stable_outbox_id("run-1:message-1") != stable_outbox_id(
        "run-1:message-2"
    )


def test_tool_idempotency_key_is_scoped_and_stable():
    assert stable_tool_idempotency_key("run-1", "answer") == (
        stable_tool_idempotency_key("run-1", "answer")
    )
    assert stable_tool_idempotency_key("run-1", "answer") != (
        stable_tool_idempotency_key("run-2", "answer")
    )
    with pytest.raises(ValueError):
        stable_tool_idempotency_key("run-1", "")


def test_record_models_detect_tampered_identity_indexes():
    envelope = inbound()
    record = InboxRecord(
        id=stable_inbox_id(envelope),
        dedupe_key=envelope.dedupe_key,
        payload_fingerprint=envelope.payload_fingerprint,
        envelope=envelope,
    )
    with pytest.raises(ValidationError, match="index key"):
        ChannelStoreState(inbox={"wrong": record})

    message = outbound()
    outbox = OutboxRecord(
        id=stable_outbox_id("key-1"),
        idempotency_key="key-1",
        payload_fingerprint=message.payload_fingerprint,
        envelope=message,
    )
    with pytest.raises(ValidationError, match="outbox index key"):
        ChannelStoreState(outbox={"wrong": outbox})
