import subprocess
from pathlib import Path

import pytest

from nanoharness.testing import ScriptedLLM

from app.host import CodexHost
from app.models import CodexJob, CodexPhase, CodexStatus, DeliveryMode

from conftest import initialize_git_repository


def _git(path: str | Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", *arguments],
        cwd=path,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _job_with_delivery(demo_job: CodexJob, mode: DeliveryMode, *, source=False):
    payload = demo_job.model_dump(mode="json")
    payload["allowed_deliveries"] = [mode.value]
    payload["phases"]["review"][0]["tool_calls"][-1]["arguments"]["mode"] = mode.value
    if source:
        payload["fixture_files"] = {}
    return CodexJob.model_validate(payload)


@pytest.fixture
def source_project(tmp_path):
    return initialize_git_repository(
        tmp_path / "source-project",
        {
            "project/brief.txt": (
                "Produce a result that says: "
                "NanoHarness profiles remain composable.\n"
            )
        },
    )


def test_host_runs_complete_white_box_flow(tmp_path, demo_job):
    result = CodexHost(demo_job, tmp_path / "run").run()

    assert result.success is True
    assert result.status == CodexStatus.COMPLETED
    assert result.phase == CodexPhase.COMPLETED
    assert result.delivery_mode == DeliveryMode.COMMIT
    assert result.delivery_commit == _git(result.active_workspace, "rev-parse", "HEAD")
    assert _git(result.active_workspace, "status", "--porcelain") == ""
    assert result.tools == [
        "delivery_submit",
        "execution_finish",
        "plan_submit",
        "review_submit",
        "workspace_read",
        "workspace_search",
        "workspace_test",
        "workspace_write",
    ]
    assert [record.tool for record in result.approvals] == [
        "workspace_write",
        "delivery_submit",
    ]
    assert all(record.approved for record in result.approvals)
    assert Path(result.artifact.trace_path).is_file()
    state = CodexHost(demo_job, tmp_path / "run").store.load()
    assert [transition.target for transition in state.transitions] == [
        CodexPhase.EXECUTE,
        CodexPhase.REVIEW,
        CodexPhase.COMPLETED,
    ]


def test_completed_run_is_idempotent(tmp_path, demo_job):
    host = CodexHost(demo_job, tmp_path / "run")
    first = host.run()
    second = CodexHost(demo_job, tmp_path / "run").run()

    assert second == first
    assert len(list((tmp_path / "run" / "artifacts").glob("**/trace.json"))) == 3


def test_provider_failure_resumes_without_replanning(tmp_path, demo_job):
    failed = {"execute": False}

    class FailOnceProvider:
        def chat(self, messages, tools=None):
            failed["execute"] = True
            raise RuntimeError("injected provider outage")

    def factory(phase, responses):
        if phase == CodexPhase.EXECUTE and not failed["execute"]:
            return FailOnceProvider()
        return ScriptedLLM(responses)

    first = CodexHost(
        demo_job,
        tmp_path / "run",
        provider_factory=factory,
    ).run()
    second = CodexHost(
        demo_job,
        tmp_path / "run",
        provider_factory=factory,
    ).run()

    assert first.status == CodexStatus.INTERRUPTED
    assert first.phase == CodexPhase.EXECUTE
    assert "provider outage" in first.error
    assert second.success is True
    state = CodexHost(demo_job, tmp_path / "run").store.load()
    assert len(state.step_task_ids) == 3
    assert [transition.source for transition in state.transitions].count(CodexPhase.PLAN) == 1


def test_denied_write_is_audited_and_can_resume(tmp_path, demo_job):
    decisions = iter([False, True, True])

    def approve(_request, _decision):
        return next(decisions)

    first = CodexHost(
        demo_job,
        tmp_path / "run",
        approve_writes=approve,
    ).run()
    second = CodexHost(
        demo_job,
        tmp_path / "run",
        approve_writes=approve,
    ).run()

    assert first.success is False
    assert first.phase == CodexPhase.EXECUTE
    assert second.success is True
    assert [record.approved for record in second.approvals] == [False, True, True]
    assert [record.tool for record in second.approvals] == [
        "workspace_write",
        "workspace_write",
        "delivery_submit",
    ]


def test_trusted_evidence_can_block_agent_pass(tmp_path, demo_job):
    payload = demo_job.model_dump(mode="json")
    payload["evidence"][0]["contains"] = "a value the implementation did not produce"
    job = CodexJob.model_validate(payload)

    result = CodexHost(job, tmp_path / "run").run()

    assert result.success is False
    assert result.status == CodexStatus.BLOCKED
    assert result.phase == CodexPhase.REVIEW
    assert result.evidence[0].passed is False
    assert result.delivery_commit is None
    assert "trusted evidence" in result.error


def test_persisted_run_rejects_changed_job(tmp_path, demo_job):
    root = tmp_path / "run"
    CodexHost(demo_job, root).run()
    changed = demo_job.model_copy(update={"objective": "A different objective"})

    with pytest.raises(ValueError, match="different job"):
        CodexHost(changed, root).run()


def test_workspace_without_state_is_rejected(tmp_path, demo_job):
    root = tmp_path / "run"
    (root / "workspace").mkdir(parents=True)
    (root / "workspace" / "ambiguous.txt").write_text("unknown", encoding="utf-8")

    with pytest.raises(ValueError, match="workspace exists without runtime/run.json"):
        CodexHost(demo_job, root).run()


def test_live_job_requires_an_explicit_provider(tmp_path, demo_job):
    live_job = demo_job.model_copy(update={"phases": {}})

    with pytest.raises(ValueError, match="provider_factory is required"):
        CodexHost(live_job, tmp_path / "run")


def test_live_provider_boundary_works_without_job_phase_scripts(tmp_path, demo_job):
    live_job = demo_job.model_copy(update={"phases": {}})
    scripts = demo_job.phases

    result = CodexHost(
        live_job,
        tmp_path / "run",
        provider_factory=lambda phase, responses: ScriptedLLM(scripts[phase]),
    ).run()

    assert result.success is True


def test_existing_repository_commit_mode_keeps_source_untouched(
    tmp_path,
    demo_job,
    source_project,
):
    job = _job_with_delivery(demo_job, DeliveryMode.COMMIT, source=True)
    source_head = _git(source_project, "rev-parse", "HEAD")

    result = CodexHost(
        job,
        tmp_path / "run",
        repository=source_project,
    ).run()

    assert result.success is True
    assert result.delivery_commit
    assert result.delivery_target_commit is None
    assert _git(source_project, "rev-parse", "HEAD") == source_head
    assert not (source_project / "project" / "result.txt").exists()
    assert (Path(result.active_workspace) / "project" / "result.txt").is_file()


def test_apply_delivery_cherry_picks_into_source(tmp_path, demo_job, source_project):
    job = _job_with_delivery(demo_job, DeliveryMode.APPLY, source=True)
    source_head = _git(source_project, "rev-parse", "HEAD")

    result = CodexHost(
        job,
        tmp_path / "run",
        repository=source_project,
    ).run()

    assert result.success is True
    assert result.delivery_mode == DeliveryMode.APPLY
    assert result.delivery_target_commit == _git(source_project, "rev-parse", "HEAD")
    assert result.delivery_target_commit != source_head
    assert (source_project / "project" / "result.txt").read_text(encoding="utf-8") == (
        "NanoHarness profiles remain composable.\n"
    )
    assert _git(source_project, "status", "--porcelain") == ""


def test_merge_delivery_creates_merge_commit_in_source(tmp_path, demo_job, source_project):
    job = _job_with_delivery(demo_job, DeliveryMode.MERGE, source=True)

    result = CodexHost(
        job,
        tmp_path / "run",
        repository=source_project,
    ).run()

    assert result.success is True
    assert result.delivery_mode == DeliveryMode.MERGE
    assert len(_git(source_project, "show", "-s", "--format=%P", "HEAD").split()) == 2
    assert "[NanoCodex:" in _git(source_project, "log", "-1", "--format=%B")


def test_keep_delivery_preserves_dirty_isolated_worktree(
    tmp_path,
    demo_job,
    source_project,
):
    job = _job_with_delivery(demo_job, DeliveryMode.KEEP, source=True)
    source_head = _git(source_project, "rev-parse", "HEAD")

    result = CodexHost(
        job,
        tmp_path / "run",
        repository=source_project,
    ).run()

    assert result.success is True
    assert result.delivery_commit is None
    assert "project/result.txt" in _git(result.active_workspace, "status", "--porcelain")
    assert _git(source_project, "rev-parse", "HEAD") == source_head


def test_source_repository_must_be_clean(tmp_path, demo_job, source_project):
    (source_project / "dirty.txt").write_text("dirty", encoding="utf-8")
    job = _job_with_delivery(demo_job, DeliveryMode.COMMIT, source=True)

    with pytest.raises(ValueError, match="must be clean"):
        CodexHost(job, tmp_path / "run", repository=source_project).run()


def test_source_repository_rejects_demo_fixtures(tmp_path, demo_job, source_project):
    with pytest.raises(ValueError, match="fixture_files"):
        CodexHost(demo_job, tmp_path / "run", repository=source_project).run()


def test_output_and_source_must_not_overlap(tmp_path, demo_job, source_project):
    job = _job_with_delivery(demo_job, DeliveryMode.COMMIT, source=True)

    with pytest.raises(ValueError, match="must not overlap"):
        CodexHost(job, source_project / ".nano-codex-run", repository=source_project)


def test_commit_refuses_unapproved_changes_before_creating_commit(tmp_path, demo_job):
    host = CodexHost(demo_job, tmp_path / "run")
    state = host._load_or_create_state()
    state.active_workspace = state.repository
    state.changed_files = ["approved.txt"]
    Path(state.repository, "approved.txt").write_text("approved\n", encoding="utf-8")
    Path(state.repository, "unexpected.txt").write_text("unexpected\n", encoding="utf-8")
    before = _git(state.repository, "rev-parse", "HEAD")

    with pytest.raises(RuntimeError, match="outside the approved file set"):
        host._commit_active_worktree(state)

    assert _git(state.repository, "rev-parse", "HEAD") == before
    assert _git(state.repository, "diff", "--cached", "--name-only") == ""


def test_commit_delivery_recovers_after_commit_before_state_save(tmp_path, demo_job):
    host = CodexHost(demo_job, tmp_path / "run")
    state = host._load_or_create_state()
    state.active_workspace = state.repository
    state.changed_files = ["space name-结果.txt"]
    state.execution_summary = "Recover commit delivery"
    Path(state.repository, state.changed_files[0]).write_text("done\n", encoding="utf-8")

    first_commit = host._commit_active_worktree(state)
    recovered_commit = host._commit_active_worktree(state)

    assert recovered_commit == first_commit
    assert _git(state.repository, "rev-list", "--count", "HEAD") == "2"


@pytest.mark.parametrize("mode", [DeliveryMode.APPLY, DeliveryMode.MERGE])
def test_source_delivery_recovers_after_git_write_before_state_save(
    tmp_path,
    demo_job,
    source_project,
    mode,
):
    job = _job_with_delivery(demo_job, mode, source=True)
    host = CodexHost(job, tmp_path / "run", repository=source_project)
    state = host._load_or_create_state()
    state.active_workspace = state.repository
    state.changed_files = ["project/result.txt"]
    state.execution_summary = f"Recover {mode.value} delivery"
    Path(state.repository, "project/result.txt").write_text("done\n", encoding="utf-8")
    state.delivery_mode = mode
    state.delivery_commit = host._commit_active_worktree(state)

    first_target = host._deliver_to_source(state)
    recovered_target = host._deliver_to_source(state)

    assert recovered_target == first_target
    assert _git(source_project, "rev-parse", "HEAD") == first_target
