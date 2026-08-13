from pathlib import Path

import pytest

from app.approvals import TerminalOutboundDecider, decide_outbound
from app.context import build_turn_context
from app.models import (
    ConversationExchange,
    ConversationRoute,
    ConversationState,
    GatewayTurnState,
    TurnPhase,
    stable_turn_id,
)
from app.policy import GatewayPolicy
from app.store import GatewayTurnStore
from app.tools import GatewayToolRuntime, register_gateway_tools
from nanoharness.components import DictToolRegistry
from nanoharness.core.schema import PolicyOutcome, PolicyStage, ToolRequest
from nanoharness.extensions.channels import InboundEnvelope, OutboundEnvelope


def envelope():
    return InboundEnvelope(
        message_id="message-1",
        channel="mock",
        account_id="primary",
        conversation_id="conversation-1",
        sender_id="user-1",
        content="hello",
    )


def make_state(tmp_path):
    route = ConversationRoute.from_envelope(envelope())
    return GatewayTurnState(
        run_id=stable_turn_id("in-1"),
        job_name="job",
        message_fingerprint="fingerprint",
        inbox_id="in-1",
        route=route,
        conversation_id=route.stable_conversation_id,
        session_id=route.stable_session_id,
        external_message_id="message-1",
        user_content="hello",
        workspace=str(tmp_path),
    )


def runtime_tools(tmp_path):
    state = make_state(tmp_path)
    store = GatewayTurnStore(tmp_path / "turn.json")
    store.save(state)
    registry = DictToolRegistry()
    register_gateway_tools(
        registry,
        GatewayToolRuntime(state, store, tmp_path),
    )
    return state, store, registry


def request(name):
    return ToolRequest(
        call_id="call-1",
        name=name,
        arguments={},
        run_id="run-1",
        session_id="session-1",
        step_id=0,
    )


def outbound(content="answer"):
    return OutboundEnvelope(
        channel="mock",
        account_id="primary",
        conversation_id="conversation-1",
        recipient_id="user-1",
        content=content,
    )


def test_registers_only_read_and_response_transition_tools(tmp_path):
    _, _, registry = runtime_tools(tmp_path)
    names = [schema["function"]["name"] for schema in registry.get_tool_schemas()]
    assert names == ["workspace_read", "response_submit"]


def test_workspace_read_reads_utf8_file(tmp_path):
    (tmp_path / "brief.txt").write_text("你好 gateway")
    _, _, registry = runtime_tools(tmp_path)
    assert registry.call("workspace_read", {"path": "brief.txt"}) == "你好 gateway"


@pytest.mark.parametrize("path", ["../secret", "/etc/passwd", ".git/config"])
def test_workspace_read_rejects_escape_and_internal_paths(tmp_path, path):
    _, _, registry = runtime_tools(tmp_path)
    with pytest.raises(ValueError):
        registry.call("workspace_read", {"path": path})


def test_workspace_read_rejects_symlink_escape(tmp_path):
    outside = tmp_path.parent / "outside.txt"
    outside.write_text("secret")
    link = tmp_path / "link.txt"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlinks are unavailable")
    _, _, registry = runtime_tools(tmp_path)
    with pytest.raises(ValueError, match="escapes"):
        registry.call("workspace_read", {"path": "link.txt"})


def test_response_submit_persists_delivery_transition(tmp_path):
    state, store, registry = runtime_tools(tmp_path)

    result = registry.call("response_submit", {"answer": " final answer "})

    assert result == "response submitted for host approval"
    assert state.phase == TurnPhase.DELIVERY
    assert state.response == "final answer"
    assert store.load().phase == TurnPhase.DELIVERY


def test_response_submit_is_idempotent_for_same_answer(tmp_path):
    _, _, registry = runtime_tools(tmp_path)
    registry.call("response_submit", {"answer": "answer"})
    assert registry.call("response_submit", {"answer": "answer"}) == (
        "response already submitted"
    )


def test_response_submit_rejects_changed_or_blank_answer(tmp_path):
    _, _, registry = runtime_tools(tmp_path)
    with pytest.raises(ValueError, match="blank"):
        registry.call("response_submit", {"answer": "  "})
    registry.call("response_submit", {"answer": "answer"})
    with pytest.raises(ValueError, match="different response"):
        registry.call("response_submit", {"answer": "changed"})


def test_gateway_policy_denies_mutation_and_model_channel_send(tmp_path):
    policy = GatewayPolicy(make_state(tmp_path))
    for name in ("workspace_write", "channel_send", "shell"):
        assert policy.decide(PolicyStage.BEFORE_TOOL, request(name)).outcome == (
            PolicyOutcome.DENY
        )


def test_gateway_policy_allows_read_and_initial_response_submit(tmp_path):
    policy = GatewayPolicy(make_state(tmp_path))
    assert policy.decide(
        PolicyStage.BEFORE_TOOL, request("workspace_read")
    ).outcome == PolicyOutcome.ALLOW
    assert policy.decide(
        PolicyStage.BEFORE_TOOL, request("response_submit")
    ).outcome == PolicyOutcome.ALLOW


def test_gateway_policy_blocks_second_response_transition(tmp_path):
    state = make_state(tmp_path)
    state.response = "answer"
    state.phase = TurnPhase.DELIVERY
    decision = GatewayPolicy(state).decide(
        PolicyStage.BEFORE_TOOL,
        request("response_submit"),
    )
    assert decision.outcome == PolicyOutcome.DENY
    assert "already" in decision.reason


def test_conversation_context_includes_only_delivered_assistant_history():
    route = ConversationRoute.from_envelope(envelope())
    conversation = ConversationState(
        conversation_id=route.stable_conversation_id,
        session_id=route.stable_session_id,
        route=route,
        exchanges=[
            ConversationExchange(
                turn_id="turn-1",
                inbox_id="in-1",
                external_message_id="message-1",
                user_content="first question",
                assistant_content="delivered answer",
                delivered=True,
            ),
            ConversationExchange(
                turn_id="turn-2",
                inbox_id="in-2",
                external_message_id="message-2",
                user_content="second question",
                assistant_content="rejected answer",
                delivered=False,
            ),
        ],
    )
    messages = build_turn_context(conversation).get_full_context()

    assert [item["role"] for item in messages] == [
        "system",
        "user",
        "assistant",
        "user",
    ]
    assert "rejected answer" not in [item["content"] for item in messages]


def test_outbound_decision_returns_explicit_reason():
    assert decide_outbound(True, outbound()) == (
        True,
        "approved by NanoOpenClaw host",
    )
    assert decide_outbound(lambda _: False, outbound())[0] is False


def test_terminal_decider_shows_metadata_and_reads_confirmation():
    written = []
    decider = TerminalOutboundDecider(
        reader=lambda _: "yes",
        writer=written.append,
    )
    assert decider(outbound("secret answer")) is True
    assert "length=13" in written[0]
