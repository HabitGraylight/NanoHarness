import pytest

from app.models import CodexPhase, CodexRunState
from app.policy import CodexPolicy
from nanoharness.core.schema import (
    PolicyOutcome,
    PolicyStage,
    ToolExecution,
    ToolRequest,
)


def _state(tmp_path, phase):
    return CodexRunState(
        job_name="policy",
        job_fingerprint="fingerprint",
        objective="test policy",
        repository=str(tmp_path),
        phase=phase,
    )


def _request(name, arguments=None):
    return ToolRequest(
        call_id=f"call_{name}",
        name=name,
        arguments=arguments or {},
        run_id="run",
        session_id="session",
        step_id=0,
    )


@pytest.mark.parametrize(
    ("phase", "allowed"),
    [
        (CodexPhase.PLAN, {"plan_submit", "workspace_search", "workspace_status"}),
        (CodexPhase.EXECUTE, {"workspace_write", "workspace_patch", "workspace_test"}),
        (CodexPhase.REVIEW, {"review_submit", "delivery_submit", "workspace_diff"}),
    ],
)
def test_phase_allowlists_admit_expected_tools(tmp_path, phase, allowed):
    policy = CodexPolicy(_state(tmp_path, phase))
    for name in allowed:
        expected = (
            PolicyOutcome.REQUIRE_APPROVAL
            if name in {"workspace_write", "workspace_patch", "delivery_submit"}
            else PolicyOutcome.ALLOW
        )
        assert policy.decide(PolicyStage.BEFORE_TOOL, _request(name)).outcome == expected


@pytest.mark.parametrize(
    ("phase", "name"),
    [
        (CodexPhase.PLAN, "workspace_write"),
        (CodexPhase.PLAN, "workspace_test"),
        (CodexPhase.EXECUTE, "plan_submit"),
        (CodexPhase.EXECUTE, "delivery_submit"),
        (CodexPhase.REVIEW, "workspace_write"),
        (CodexPhase.REVIEW, "execution_finish"),
    ],
)
def test_phase_allowlists_block_cross_phase_tools(tmp_path, phase, name):
    decision = CodexPolicy(_state(tmp_path, phase)).decide(
        PolicyStage.BEFORE_TOOL,
        _request(name),
    )
    assert decision.outcome == PolicyOutcome.DENY
    assert decision.metadata == {"execution_status": "blocked", "phase": phase.value}


def test_channel_is_always_denied(tmp_path):
    for phase in (CodexPhase.PLAN, CodexPhase.EXECUTE, CodexPhase.REVIEW):
        decision = CodexPolicy(_state(tmp_path, phase)).decide(
            PolicyStage.BEFORE_TOOL,
            _request("channel_send"),
        )
        assert decision.outcome == PolicyOutcome.DENY


def test_after_tool_is_allowed_even_after_phase_transition(tmp_path):
    state = _state(tmp_path, CodexPhase.EXECUTE)
    policy = CodexPolicy(state)
    state.phase = CodexPhase.REVIEW
    decision = policy.decide(
        PolicyStage.AFTER_TOOL,
        _request("execution_finish"),
        ToolExecution(call_id="call", name="execution_finish"),
    )
    assert decision.outcome == PolicyOutcome.ALLOW


def test_policy_without_state_preserves_generic_write_approval():
    policy = CodexPolicy()
    assert policy.decide(
        PolicyStage.BEFORE_TOOL,
        _request("workspace_write"),
    ).outcome == PolicyOutcome.REQUIRE_APPROVAL
    assert policy.decide(
        PolicyStage.BEFORE_TOOL,
        _request("anything_else"),
    ).outcome == PolicyOutcome.ALLOW
