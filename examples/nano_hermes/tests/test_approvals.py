from nanoharness.core.schema import PolicyDecision, PolicyOutcome, ToolRequest

from app.approvals import (
    RecordingActionApprovalBroker,
    TerminalActionDecider,
    TerminalLearningDecider,
)
from app.models import HermesRunState, LearningProposal, ProposalKind
from app.store import HermesRunStore


def _state(demo_job, tmp_path):
    return HermesRunState(
        job_name=demo_job.name,
        job_fingerprint=demo_job.fingerprint(),
        query=demo_job.query,
        workspace=str(tmp_path),
    )


def _request(name, arguments):
    return ToolRequest(
        call_id="call_1",
        name=name,
        arguments=arguments,
        run_id="run",
        session_id="session",
        step_id=0,
    )


def test_action_broker_records_safe_metadata_without_content(tmp_path, demo_job):
    state = _state(demo_job, tmp_path)
    store = HermesRunStore(tmp_path / "run.json")
    broker = RecordingActionApprovalBroker(state, store, True)
    request = _request(
        "workspace_write",
        {"path": "notes.md", "content": "private"},
    )

    result = broker.request_approval(
        request,
        PolicyDecision(outcome=PolicyOutcome.REQUIRE_APPROVAL, reason="review"),
    )

    assert result.status.value == "approved"
    assert state.action_approvals[0].details == {"path": "notes.md"}
    assert "private" not in store.path.read_text(encoding="utf-8")


def test_action_broker_calls_decider_and_records_denial(tmp_path, demo_job):
    state = _state(demo_job, tmp_path)
    store = HermesRunStore(tmp_path / "run.json")
    seen = []
    broker = RecordingActionApprovalBroker(
        state,
        store,
        lambda request, decision: seen.append((request.name, decision.reason)) or False,
    )

    result = broker.request_approval(
        _request("schedule_create", {}),
        PolicyDecision(outcome=PolicyOutcome.REQUIRE_APPROVAL, reason="review"),
    )

    assert result.status.value == "denied"
    assert seen == [("schedule_create", "review")]
    assert state.action_approvals[0].approved is False


def test_terminal_action_decider_defaults_no_and_redacts_prompt():
    output = []
    prompts = []
    decider = TerminalActionDecider(
        input_func=lambda prompt: prompts.append(prompt) or "",
        output_func=output.append,
    )
    approved = decider(
        _request("workspace_write", {"path": "a.txt", "content": "secret"}),
        PolicyDecision(outcome=PolicyOutcome.REQUIRE_APPROVAL, reason="review"),
    )
    assert approved is False
    assert "a.txt" in prompts[0]
    assert "secret" not in prompts[0]


def test_terminal_learning_decider_shows_hash_not_content():
    output = []
    proposal = LearningProposal(
        kind=ProposalKind.SKILL,
        name="review",
        content="private instructions",
        source_run_id="run",
    )
    approved = TerminalLearningDecider(
        input_func=lambda _prompt: "yes",
        output_func=output.append,
    )(proposal)
    assert approved is True
    assert proposal.content_sha256 in output[0]
    assert "private instructions" not in output[0]
