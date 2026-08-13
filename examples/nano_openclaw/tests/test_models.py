from pathlib import Path

import pytest
from pydantic import ValidationError

from app.models import (
    ConversationRoute,
    ConversationState,
    GatewayJob,
    GatewayTurnState,
    TurnPhase,
    TurnStatus,
    stable_turn_id,
)
from nanoharness.extensions.channels import InboundEnvelope


def envelope(message_id="message-1", sender_id="user-1", content="hello"):
    return InboundEnvelope(
        message_id=message_id,
        channel="mock",
        account_id="primary",
        conversation_id="conversation-1",
        sender_id=sender_id,
        content=content,
        received_at="2026-08-07T12:00:00Z",
    )


def job_payload():
    return {
        "name": "test-job",
        "messages": [
            {
                "envelope": envelope().model_dump(mode="json"),
                "responses": [{"content": "done"}],
            }
        ],
    }


def turn_state(**updates):
    route = ConversationRoute.from_envelope(envelope())
    payload = dict(
        run_id=stable_turn_id("in_test"),
        job_name="test-job",
        message_fingerprint="abc",
        inbox_id="in_test",
        route=route,
        conversation_id=route.stable_conversation_id,
        session_id=route.stable_session_id,
        external_message_id="message-1",
        user_content="hello",
        workspace=str(Path.cwd()),
    )
    payload.update(updates)
    return GatewayTurnState(**payload)


def test_route_identity_is_stable_for_same_transport_tuple():
    first = ConversationRoute.from_envelope(envelope())
    second = ConversationRoute.from_envelope(envelope("message-2"))

    assert first.key == second.key
    assert first.stable_conversation_id == second.stable_conversation_id
    assert first.stable_session_id == second.stable_session_id


def test_route_isolates_senders_even_in_same_channel_conversation():
    first = ConversationRoute.from_envelope(envelope(sender_id="user-1"))
    second = ConversationRoute.from_envelope(envelope(sender_id="user-2"))

    assert first.stable_conversation_id != second.stable_conversation_id
    assert first.stable_session_id != second.stable_session_id


def test_conversation_rejects_mismatched_stable_identity():
    route = ConversationRoute.from_envelope(envelope())
    with pytest.raises(ValidationError, match="conversation id"):
        ConversationState(
            conversation_id="wrong",
            session_id=route.stable_session_id,
            route=route,
        )


def test_job_rejects_duplicate_channel_message_identity():
    payload = job_payload()
    payload["messages"].append(payload["messages"][0])
    with pytest.raises(ValidationError, match="unique channel identities"):
        GatewayJob.model_validate(payload)


@pytest.mark.parametrize("path", ["../escape.txt", "/tmp/escape", "a/../../b"])
def test_job_rejects_unsafe_fixture_paths(path):
    payload = job_payload()
    payload["fixture_files"] = {path: "bad"}
    with pytest.raises(ValidationError, match="inside the workspace"):
        GatewayJob.model_validate(payload)


def test_job_materialization_is_idempotent(tmp_path):
    payload = job_payload()
    payload["fixture_files"] = {"project/brief.txt": "brief"}
    job = GatewayJob.model_validate(payload)

    job.materialize(tmp_path)
    job.materialize(tmp_path)

    assert (tmp_path / "project" / "brief.txt").read_text() == "brief"


def test_job_materialization_rejects_conflicting_persistent_fixture(tmp_path):
    payload = job_payload()
    payload["fixture_files"] = {"brief.txt": "expected"}
    job = GatewayJob.model_validate(payload)
    (tmp_path / "brief.txt").write_text("different")

    with pytest.raises(ValueError, match="conflicts"):
        job.materialize(tmp_path)


def test_message_fingerprint_ignores_transport_receive_time():
    payload = job_payload()
    first = GatewayJob.model_validate(payload).messages[0]
    payload["messages"][0]["envelope"]["received_at"] = "2026-08-07T13:00:00Z"
    second = GatewayJob.model_validate(payload).messages[0]

    assert first.fingerprint() == second.fingerprint()


def test_turn_id_is_deterministic_and_input_specific():
    assert stable_turn_id("in_one") == stable_turn_id("in_one")
    assert stable_turn_id("in_one") != stable_turn_id("in_two")


def test_turn_delivery_phase_requires_a_response():
    with pytest.raises(ValidationError, match="require a response"):
        turn_state(phase=TurnPhase.DELIVERY)


def test_completed_status_requires_completed_phase():
    with pytest.raises(ValidationError, match="completed phase"):
        turn_state(status=TurnStatus.COMPLETED)


def test_job_file_must_contain_an_object(tmp_path):
    path = tmp_path / "job.yaml"
    path.write_text("- not\n- an\n- object\n")
    with pytest.raises(ValueError, match="YAML object"):
        GatewayJob.from_file(path)
