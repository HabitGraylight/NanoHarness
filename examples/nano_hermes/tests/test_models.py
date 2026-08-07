import json

import pytest
from pydantic import ValidationError

from app.models import (
    HERMES_JOB_VERSION,
    HERMES_STATE_VERSION,
    HermesJob,
    HermesPhase,
    HermesRunKind,
    HermesRunState,
    LearningProposal,
    ProposalKind,
    content_sha256,
    safe_relative_path,
)


@pytest.mark.parametrize("path", ["../outside", "/absolute", "a/../../b"])
def test_safe_relative_path_rejects_escape(path):
    with pytest.raises(ValueError, match="inside the workspace"):
        safe_relative_path(path)


def test_content_hash_is_stable_and_sensitive():
    assert content_sha256("same") == content_sha256("same")
    assert content_sha256("same") != content_sha256("different")


def test_job_allows_provider_driven_phases(demo_job):
    live = demo_job.model_copy(update={"phases": {}})
    assert live.scripted is False


def test_job_requires_both_scripts_when_scripted(demo_job):
    payload = demo_job.model_dump(mode="json")
    del payload["phases"][HermesPhase.REFLECT.value]
    with pytest.raises(ValidationError, match="missing phase scripts"):
        HermesJob.model_validate(payload)


def test_job_rejects_empty_or_host_only_phase_scripts(demo_job):
    payload = demo_job.model_dump(mode="json")
    payload["phases"][HermesPhase.ASSIST.value] = []
    with pytest.raises(ValidationError, match="cannot be empty"):
        HermesJob.model_validate(payload)
    payload = demo_job.model_dump(mode="json")
    payload["phases"][HermesPhase.REVIEW.value] = payload["phases"][
        HermesPhase.REFLECT.value
    ]
    with pytest.raises(ValidationError, match="unsupported phase scripts"):
        HermesJob.model_validate(payload)


def test_job_rejects_unsafe_fixture(demo_job):
    payload = demo_job.model_dump(mode="json")
    payload["fixture_files"] = {"../outside": "unsafe"}
    with pytest.raises(ValidationError, match="inside the workspace"):
        HermesJob.model_validate(payload)


def test_scheduled_job_requires_id_and_user_job_rejects_it(demo_job):
    payload = demo_job.model_dump(mode="json")
    payload["run_kind"] = "scheduled"
    with pytest.raises(ValidationError, match="require schedule_id"):
        HermesJob.model_validate(payload)
    payload = demo_job.model_dump(mode="json")
    payload["schedule_id"] = 1
    with pytest.raises(ValidationError, match="cannot declare schedule_id"):
        HermesJob.model_validate(payload)


def test_job_fingerprint_is_stable_and_content_sensitive(demo_job):
    assert demo_job.fingerprint() == demo_job.model_copy(deep=True).fingerprint()
    changed = demo_job.model_copy(update={"query": demo_job.query + " changed"})
    assert changed.fingerprint() != demo_job.fingerprint()


def test_job_materialize_is_persistent_and_conflict_safe(demo_job, tmp_path):
    workspace = tmp_path / "workspace"
    demo_job.materialize(workspace)
    demo_job.materialize(workspace)
    target = workspace / "project" / "brief.txt"
    target.write_text("different", encoding="utf-8")
    with pytest.raises(ValueError, match="conflicts"):
        demo_job.materialize(workspace)


def test_job_rejects_unsupported_version(demo_job):
    payload = demo_job.model_dump(mode="json")
    payload["schema_version"] = "99"
    with pytest.raises(ValidationError, match=HERMES_JOB_VERSION):
        HermesJob.model_validate(payload)


@pytest.mark.parametrize("name", ["../escape", "bad name", "_leading", "x" * 65])
def test_learning_proposal_rejects_unsafe_name(name):
    with pytest.raises(ValidationError, match="unsupported characters"):
        LearningProposal(
            kind=ProposalKind.MEMORY,
            name=name,
            content="content",
            source_run_id="run",
        )


def test_learning_proposal_computes_and_verifies_content_hash():
    proposal = LearningProposal(
        kind=ProposalKind.SKILL,
        name="safe-name",
        content="content",
        source_run_id="run",
    )
    assert proposal.content_sha256 == content_sha256("content")
    payload = proposal.model_dump(mode="json")
    payload["content_sha256"] = "0" * 64
    with pytest.raises(ValidationError, match="does not match"):
        LearningProposal.model_validate(payload)


def test_memory_proposal_rejects_trigger_and_bad_type():
    with pytest.raises(ValidationError, match="cannot declare a trigger"):
        LearningProposal(
            kind="memory",
            name="memory",
            content="content",
            trigger="when needed",
            source_run_id="run",
        )
    with pytest.raises(ValidationError, match="unsupported memory type"):
        LearningProposal(
            kind="memory",
            name="memory",
            content="content",
            memory_type="secret",
            source_run_id="run",
        )


def test_state_round_trip_and_version_guard(demo_job):
    state = HermesRunState(
        job_name=demo_job.name,
        job_fingerprint=demo_job.fingerprint(),
        query=demo_job.query,
        workspace="/tmp/workspace",
    )
    loaded = HermesRunState.model_validate_json(
        json.dumps(state.model_dump(mode="json"))
    )
    assert loaded.run_id == state.run_id
    payload = state.model_dump(mode="json")
    payload["schema_version"] = "99"
    with pytest.raises(ValidationError, match=HERMES_STATE_VERSION):
        HermesRunState.model_validate(payload)
