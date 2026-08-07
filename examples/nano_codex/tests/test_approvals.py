from app.approvals import RecordingApprovalBroker, TerminalApprovalDecider
from app.models import CodexRunState
from app.store import CodexRunStore
from nanoharness.core.schema import (
    ApprovalStatus,
    PolicyDecision,
    PolicyOutcome,
    ToolRequest,
)


def _request(name="workspace_write", arguments=None):
    return ToolRequest(
        call_id="call_approval",
        name=name,
        arguments=arguments or {"path": "a.txt", "content": "secret"},
        run_id="run",
        session_id="session",
        step_id=0,
    )


def _state(tmp_path):
    return CodexRunState(
        job_name="approval",
        job_fingerprint="fingerprint",
        objective="approve",
        repository=str(tmp_path),
    )


def _decision():
    return PolicyDecision(
        outcome=PolicyOutcome.REQUIRE_APPROVAL,
        reason="approval required",
    )


def test_recording_broker_persists_approval_without_content(tmp_path):
    state = _state(tmp_path)
    store = CodexRunStore(tmp_path / "run.json")
    result = RecordingApprovalBroker(state, store, True).request_approval(
        _request(), _decision()
    )
    assert result.status == ApprovalStatus.APPROVED
    record = store.load().approvals[0]
    assert record.path == "a.txt"
    assert record.details == {"path": "a.txt"}
    assert "secret" not in (tmp_path / "run.json").read_text(encoding="utf-8")


def test_recording_broker_records_denial_and_callback(tmp_path):
    state = _state(tmp_path)
    seen = []

    def decide(request, decision):
        seen.append((request.name, decision.reason))
        return False

    result = RecordingApprovalBroker(
        state,
        CodexRunStore(tmp_path / "run.json"),
        decide,
    ).request_approval(_request(), _decision())
    assert result.status == ApprovalStatus.DENIED
    assert state.approvals[0].approved is False
    assert seen == [("workspace_write", "approval required")]


def test_delivery_approval_records_mode_only(tmp_path):
    state = _state(tmp_path)
    RecordingApprovalBroker(
        state,
        CodexRunStore(tmp_path / "run.json"),
        True,
    ).request_approval(
        _request("delivery_submit", {"mode": "merge", "content": "ignored"}),
        _decision(),
    )
    assert state.approvals[0].details == {"mode": "merge"}


def test_terminal_decider_accepts_yes_and_defaults_to_no():
    output = []
    yes = TerminalApprovalDecider(lambda _prompt: "yes", output.append)
    no = TerminalApprovalDecider(lambda _prompt: "", output.append)
    assert yes(_request(), _decision()) is True
    assert no(_request(), _decision()) is False
    assert output == ["approval required", "approval required"]


def test_terminal_prompt_does_not_include_content():
    prompts = []
    decider = TerminalApprovalDecider(
        lambda prompt: prompts.append(prompt) or "n",
        lambda _message: None,
    )
    decider(_request(arguments={"path": "a.txt", "content": "top-secret"}), _decision())
    assert "a.txt" in prompts[0]
    assert "top-secret" not in prompts[0]
