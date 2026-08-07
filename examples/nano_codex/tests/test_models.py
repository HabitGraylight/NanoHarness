import json

import pytest
from pydantic import ValidationError

from app.models import (
    CODEX_JOB_VERSION,
    CodexJob,
    CodexPhase,
    DeliveryMode,
    EvidenceCheck,
    TrustedCommand,
)


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({"kind": "file_exists"}, "requires path"),
        ({"kind": "file_contains", "path": "x"}, "requires contains"),
        ({"kind": "command"}, "non-empty command"),
        ({"kind": "command", "command": ["true"], "path": "x"}, "cannot declare"),
        ({"kind": "file_exists", "path": "x", "command": ["true"]}, "cannot declare"),
    ],
)
def test_evidence_payload_validation(payload, message):
    with pytest.raises(ValidationError, match=message):
        EvidenceCheck.model_validate(payload)


@pytest.mark.parametrize("path", ["../outside", "/absolute", "a/../../b"])
def test_evidence_rejects_workspace_escape(path):
    with pytest.raises(ValidationError, match="inside the workspace"):
        EvidenceCheck(kind="file_exists", path=path)


def test_all_evidence_kinds_accept_valid_payloads():
    assert EvidenceCheck(kind="file_exists", path="a.txt").path == "a.txt"
    assert EvidenceCheck(
        kind="file_contains", path="a.txt", contains="needle"
    ).contains == "needle"
    assert EvidenceCheck(kind="command", command=["git", "status"]).command[0] == "git"


def test_trusted_command_rejects_empty_argv_items():
    with pytest.raises(ValidationError, match="cannot be empty"):
        TrustedCommand(argv=["git", ""])


def test_job_allows_live_provider_without_scripted_phases(demo_job):
    live = demo_job.model_copy(update={"phases": {}})
    assert live.scripted is False


def test_job_requires_all_phases_when_any_are_scripted(demo_job):
    payload = demo_job.model_dump(mode="json")
    del payload["phases"][CodexPhase.REVIEW.value]
    with pytest.raises(ValidationError, match="missing phase scripts"):
        CodexJob.model_validate(payload)


def test_job_rejects_empty_phase_script(demo_job):
    payload = demo_job.model_dump(mode="json")
    payload["phases"][CodexPhase.PLAN.value] = []
    with pytest.raises(ValidationError, match="cannot be empty"):
        CodexJob.model_validate(payload)


def test_job_rejects_completed_phase_script(demo_job):
    payload = demo_job.model_dump(mode="json")
    payload["phases"][CodexPhase.COMPLETED.value] = payload["phases"][
        CodexPhase.REVIEW.value
    ]
    with pytest.raises(ValidationError, match="unsupported phase scripts"):
        CodexJob.model_validate(payload)


def test_job_rejects_unsafe_fixture_and_command_names(demo_job):
    payload = demo_job.model_dump(mode="json")
    payload["fixture_files"] = {"../outside": "unsafe"}
    with pytest.raises(ValidationError, match="inside the workspace"):
        CodexJob.model_validate(payload)
    payload = demo_job.model_dump(mode="json")
    payload["commands"] = {"bad name": {"argv": ["true"]}}
    with pytest.raises(ValidationError, match="unsafe command name"):
        CodexJob.model_validate(payload)


def test_job_rejects_empty_or_duplicate_delivery_modes(demo_job):
    payload = demo_job.model_dump(mode="json")
    payload["allowed_deliveries"] = []
    with pytest.raises(ValidationError, match="cannot be empty"):
        CodexJob.model_validate(payload)
    payload["allowed_deliveries"] = ["keep", "keep"]
    with pytest.raises(ValidationError, match="duplicates"):
        CodexJob.model_validate(payload)


def test_job_fingerprint_is_stable_and_content_sensitive(demo_job):
    assert demo_job.fingerprint() == demo_job.model_copy(deep=True).fingerprint()
    changed = demo_job.model_copy(update={"objective": demo_job.objective + " changed"})
    assert changed.fingerprint() != demo_job.fingerprint()


def test_job_materialize_and_from_file_round_trip(demo_job, tmp_path):
    workspace = tmp_path / "workspace"
    demo_job.materialize(workspace)
    assert (workspace / "project" / "brief.txt").is_file()
    path = tmp_path / "job.json"
    path.write_text(json.dumps(demo_job.model_dump(mode="json")), encoding="utf-8")
    loaded = CodexJob.model_validate_json(path.read_text(encoding="utf-8"))
    assert loaded.fingerprint() == demo_job.fingerprint()


def test_job_rejects_unsupported_version(demo_job):
    payload = demo_job.model_dump(mode="json")
    payload["schema_version"] = "99"
    with pytest.raises(ValidationError, match=CODEX_JOB_VERSION):
        CodexJob.model_validate(payload)


def test_demo_job_declares_controlled_commands_and_delivery(demo_job):
    assert "diff-check" in demo_job.commands
    assert demo_job.allowed_deliveries == [DeliveryMode.COMMIT]
