import json
from pathlib import Path

from main import run_demo
from nanoharness.extensions.channels import InboxStatus, OutboxStatus


def test_nano_openclaw_runs_two_durable_conversation_turns(tmp_path):
    result = run_demo(tmp_path)

    assert result.profile == "nano-openclaw"
    assert result.success is True
    assert result.processed == 2
    assert result.delivered == 2
    assert result.turns[0].tools == ["response_submit", "workspace_read"]
    assert result.turns[1].tools == ["response_submit"]
    assert result.turns[0].conversation_id == result.turns[1].conversation_id
    assert result.turns[0].session_id == result.turns[1].session_id
    assert all(turn.delivery_status == OutboxStatus.SENT for turn in result.turns)
    assert all(Path(turn.artifact.trace_path).exists() for turn in result.turns)


def test_demo_persists_completed_inbox_outbox_turn_and_conversation(tmp_path):
    result = run_demo(tmp_path)
    channel_state = json.loads(
        (tmp_path / "runtime" / "channels" / "state.json").read_text()
    )
    conversation_state = json.loads(
        (tmp_path / "runtime" / "conversations.json").read_text()
    )

    assert {item["status"] for item in channel_state["inbox"].values()} == {
        InboxStatus.COMPLETED.value
    }
    assert {item["status"] for item in channel_state["outbox"].values()} == {
        OutboxStatus.SENT.value
    }
    exchanges = next(iter(conversation_state.values()))["exchanges"]
    assert [exchange["delivered"] for exchange in exchanges] == [True, True]
    assert [exchange["turn_id"] for exchange in exchanges] == [
        turn.run_id for turn in result.turns
    ]


def test_demo_replay_is_idempotent(tmp_path):
    first = run_demo(tmp_path)
    second = run_demo(tmp_path)

    assert [turn.run_id for turn in second.turns] == [
        turn.run_id for turn in first.turns
    ]
    assert [turn.outbox_id for turn in second.turns] == [
        turn.outbox_id for turn in first.turns
    ]
    channel_state = json.loads(
        (tmp_path / "runtime" / "channels" / "state.json").read_text()
    )
    assert len(channel_state["inbox"]) == 2
    assert len(channel_state["outbox"]) == 2
