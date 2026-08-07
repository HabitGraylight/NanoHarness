from pathlib import Path

import pytest

from nanoharness.extensions.memory import FileMemoryManager
from nanoharness.extensions.scheduler import Scheduler
from nanoharness.extensions.skills import SkillRegistry
from nanoharness.testing import ScriptedLLM

from app.host import HermesHost
from app.models import (
    HermesJob,
    HermesPhase,
    HermesRunKind,
    HermesStatus,
)


def _simple_job(demo_job, *, name="simple", query="Use durable learning."):
    payload = demo_job.model_dump(mode="json")
    payload.update({
        "name": name,
        "query": query,
        "fixture_files": {},
        "phases": {
            "assist": [
                {
                    "content": "I will answer.",
                    "tool_calls": [
                        {
                            "call_id": f"{name}_assist",
                            "name": "assist_submit",
                            "arguments": {"answer": f"answer for {query}"},
                        }
                    ],
                },
                {"content": "Answer submitted."},
            ],
            "reflect": [
                {
                    "content": "No new durable learning.",
                    "tool_calls": [
                        {
                            "call_id": f"{name}_reflect",
                            "name": "reflection_submit",
                            "arguments": {"summary": "No proposals."},
                        }
                    ],
                },
                {"content": "Reflection submitted."},
            ],
        },
    })
    return HermesJob.model_validate(payload)


def _recall_job(demo_job):
    payload = _simple_job(demo_job, name="recall").model_dump(mode="json")
    payload["phases"]["assist"][0]["tool_calls"] = [
        {
            "call_id": "recall_memory_1",
            "name": "recall_memory",
            "arguments": {"query": "Durable learning"},
        },
        {
            "call_id": "recall_skill_1",
            "name": "skill",
            "arguments": {"name": "review-durable-learning"},
        },
        {
            "call_id": "recall_submit_1",
            "name": "assist_submit",
            "arguments": {"answer": "Reused promoted memory and skill."},
        },
    ]
    return HermesJob.model_validate(payload)


def test_host_runs_complete_learning_loop(tmp_path, demo_job):
    result = HermesHost(demo_job, tmp_path / "hermes").run()

    assert result.profile == "nano-hermes"
    assert result.success is True
    assert result.phase == HermesPhase.COMPLETED
    assert result.status == HermesStatus.COMPLETED
    assert result.total_steps == 4
    assert result.tools == [
        "assist_submit",
        "memory_propose",
        "reflection_submit",
        "schedule_create",
        "skill_propose",
        "workspace_read",
    ]
    assert result.promoted == [
        "memory:durable-learning-boundary",
        "skill:review-durable-learning",
    ]
    assert [decision.approved for decision in result.decisions] == [True, True]
    assert [approval.tool for approval in result.action_approvals] == [
        "schedule_create"
    ]
    assert all(Path(item.trace_path).is_file() for item in result.artifacts)


def test_learning_persists_and_is_reused_across_runs(tmp_path, demo_job):
    root = tmp_path / "hermes"
    learned = HermesHost(demo_job, root).run()
    recalled = HermesHost(_recall_job(demo_job), root).run()

    assert learned.success and recalled.success
    assert "recall_memory" in recalled.tools
    assert "skill" in recalled.tools
    assert recalled.response == "Reused promoted memory and skill."
    memories = FileMemoryManager(str(root / "runtime" / "memory")).list_all()
    assert [item.name for item in memories] == ["durable-learning-boundary"]
    assert "review-durable-learning" in SkillRegistry(
        str(root / "runtime" / "skills")
    ).list_names()


def test_learning_denial_completes_task_without_catalog_mutation(tmp_path, demo_job):
    root = tmp_path / "hermes"
    result = HermesHost(demo_job, root, approve_learning=False).run()

    assert result.success is True
    assert result.promoted == []
    assert result.rejected == [
        "memory:durable-learning-boundary",
        "skill:review-durable-learning",
    ]
    assert FileMemoryManager(str(root / "runtime" / "memory")).list_all() == []
    assert "review-durable-learning" not in SkillRegistry(
        str(root / "runtime" / "skills")
    ).list_names()


def test_action_denial_is_audited_but_does_not_block_response(tmp_path, demo_job):
    root = tmp_path / "hermes"
    result = HermesHost(demo_job, root, approve_actions=False).run()

    assert result.success is True
    assert result.action_approvals[0].approved is False
    scheduler = Scheduler(
        persist_path=str(root / "runtime" / "schedules.json"),
        start_checker=False,
    )
    try:
        assert scheduler.list() == []
    finally:
        scheduler.stop()


def test_provider_interruption_resumes_same_reflection_without_new_run(
    tmp_path,
    demo_job,
):
    failed = {"reflect": False}

    class FailOnce:
        def chat(self, messages, tools=None):
            failed["reflect"] = True
            raise RuntimeError("injected reflection outage")

    def factory(phase, responses):
        if phase == HermesPhase.REFLECT and not failed["reflect"]:
            return FailOnce()
        return ScriptedLLM(responses)

    root = tmp_path / "hermes"
    first = HermesHost(demo_job, root, provider_factory=factory).run()
    resumed = HermesHost(
        demo_job,
        root,
        resume_run_id=first.run_id,
        provider_factory=factory,
    ).run()

    assert first.status == HermesStatus.INTERRUPTED
    assert first.phase == HermesPhase.REFLECT
    assert resumed.success is True
    assert resumed.run_id == first.run_id
    assert len(resumed.artifacts) == 2


def test_completed_run_resume_is_idempotent(tmp_path, demo_job):
    root = tmp_path / "hermes"
    first = HermesHost(demo_job, root).run()
    resumed = HermesHost(demo_job, root, resume_run_id=first.run_id).run()

    assert resumed == first
    assert len(list((root / "runtime" / "runs").glob("*.json"))) == 1


def test_new_host_invocation_creates_independent_run(tmp_path, demo_job):
    root = tmp_path / "hermes"
    first = HermesHost(_simple_job(demo_job, name="one"), root).run()
    second = HermesHost(_simple_job(demo_job, name="two"), root).run()

    assert first.run_id != second.run_id
    assert len(list((root / "runtime" / "runs").glob("*.json"))) == 2


def test_resume_rejects_missing_or_different_job(tmp_path, demo_job):
    root = tmp_path / "hermes"
    first = HermesHost(_simple_job(demo_job), root).run()
    with pytest.raises(ValueError, match="run not found"):
        HermesHost(demo_job, root, resume_run_id="missing").run()
    with pytest.raises(ValueError, match="different job"):
        HermesHost(demo_job, root, resume_run_id=first.run_id).run()


def test_provider_driven_job_requires_explicit_provider(tmp_path, demo_job):
    live = demo_job.model_copy(update={"phases": {}})
    with pytest.raises(ValueError, match="provider_factory is required"):
        HermesHost(live, tmp_path / "hermes")


def test_provider_boundary_runs_live_job_without_embedded_scripts(tmp_path, demo_job):
    live = _simple_job(demo_job).model_copy(update={"phases": {}})
    scripts = _simple_job(demo_job).phases

    result = HermesHost(
        live,
        tmp_path / "hermes",
        provider_factory=lambda phase, responses: ScriptedLLM(scripts[phase]),
    ).run()

    assert result.success is True


def test_seed_skills_are_not_overwritten_between_runs(tmp_path, demo_job):
    root = tmp_path / "hermes"
    HermesHost(_simple_job(demo_job), root).run()
    seeded = root / "runtime" / "skills" / "harness-inspection.md"
    seeded.write_text("user customized", encoding="utf-8")

    HermesHost(_simple_job(demo_job, name="second"), root).run()

    assert seeded.read_text(encoding="utf-8") == "user customized"


def test_due_schedule_runs_as_independent_scheduled_trigger(tmp_path, demo_job):
    root = tmp_path / "hermes"
    scheduler = Scheduler(
        persist_path=str(root / "runtime" / "schedules.json"),
        start_checker=False,
    )
    try:
        scheduler.create("Run scheduled reflection", delay_seconds=0)
    finally:
        scheduler.stop()
    template = _simple_job(demo_job, name="scheduled-template")
    host = HermesHost(template, root)

    results = host.run_due(template)

    assert len(results) == 1
    assert results[0].success is True
    assert results[0].run_kind == HermesRunKind.SCHEDULED
    state = Path(results[0].state_path).read_text(encoding="utf-8")
    assert "Run scheduled reflection" in state
    assert host.run_due(template) == []


def test_persistent_fixture_conflict_is_never_overwritten(tmp_path, demo_job):
    root = tmp_path / "hermes"
    HermesHost(demo_job, root).run()
    brief = root / "workspace" / "project" / "brief.txt"
    brief.write_text("user changed", encoding="utf-8")

    with pytest.raises(ValueError, match="fixture conflicts"):
        HermesHost(demo_job, root).run()
