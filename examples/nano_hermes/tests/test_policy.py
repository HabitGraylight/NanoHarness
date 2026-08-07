import pytest

from nanoharness.core.schema import (
    PolicyOutcome,
    PolicyStage,
    ToolRequest,
)

from app.models import HermesPhase, HermesRunState
from app.policy import HermesPolicy


def _state(demo_job, tmp_path, phase):
    return HermesRunState(
        job_name=demo_job.name,
        job_fingerprint=demo_job.fingerprint(),
        query=demo_job.query,
        workspace=str(tmp_path),
        phase=phase,
    )


def _decide(policy, name, stage=PolicyStage.BEFORE_TOOL):
    return policy.decide(
        stage,
        ToolRequest(
            call_id="call",
            name=name,
            arguments={},
            run_id="run",
            session_id="session",
            step_id=0,
        ),
    )


@pytest.mark.parametrize("name", ["channel_send", "save_memory"])
def test_policy_always_denies_unreviewed_durable_or_external_effects(name):
    decision = _decide(HermesPolicy(), name)
    assert decision.outcome == PolicyOutcome.DENY


@pytest.mark.parametrize(
    "name",
    [
        "workspace_write",
        "schedule_create",
        "schedule_pause",
        "schedule_resume",
        "schedule_delete",
    ],
)
def test_policy_requires_approval_for_persistent_actions(name):
    decision = _decide(HermesPolicy(), name)
    assert decision.outcome == PolicyOutcome.REQUIRE_APPROVAL


def test_assist_allows_recall_schedule_listing_and_delegation(tmp_path, demo_job):
    policy = HermesPolicy(_state(demo_job, tmp_path, HermesPhase.ASSIST))
    for name in ("recall_memory", "schedule_list", "skill", "task", "assist_submit"):
        assert _decide(policy, name).outcome == PolicyOutcome.ALLOW


def test_assist_cannot_propose_learning(tmp_path, demo_job):
    policy = HermesPolicy(_state(demo_job, tmp_path, HermesPhase.ASSIST))
    decision = _decide(policy, "memory_propose")
    assert decision.outcome == PolicyOutcome.DENY
    assert decision.metadata["phase"] == "assist"


def test_reflect_allows_proposals_but_not_workspace_mutation(tmp_path, demo_job):
    policy = HermesPolicy(_state(demo_job, tmp_path, HermesPhase.REFLECT))
    assert _decide(policy, "memory_propose").outcome == PolicyOutcome.ALLOW
    assert _decide(policy, "skill_propose").outcome == PolicyOutcome.ALLOW
    assert _decide(policy, "workspace_write").outcome == PolicyOutcome.DENY


def test_after_tool_is_always_allowed(tmp_path, demo_job):
    policy = HermesPolicy(_state(demo_job, tmp_path, HermesPhase.REFLECT))
    assert _decide(policy, "anything", PolicyStage.AFTER_TOOL).outcome == (
        PolicyOutcome.ALLOW
    )
