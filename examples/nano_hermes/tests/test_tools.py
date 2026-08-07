from pathlib import Path

import pytest

from nanoharness.components import DictToolRegistry
from nanoharness.extensions import ExtensionContext

from app.models import HermesPhase, HermesRunState, ProposalKind
from app.store import HermesRunStore
from app.tools import HermesToolRuntime, register_hermes_tools


@pytest.fixture
def tool_runtime(tmp_path, demo_job):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    state = HermesRunState(
        job_name=demo_job.name,
        job_fingerprint=demo_job.fingerprint(),
        query=demo_job.query,
        workspace=str(workspace),
    )
    store = HermesRunStore(tmp_path / "runtime" / "run.json")
    store.save(state)
    registry = DictToolRegistry()
    context = ExtensionContext(
        tools=registry,
        services={},
        capabilities=set(),
        metadata={},
    )
    runtime = HermesToolRuntime(
        state=state,
        store=store,
        context=context,
        workspace=workspace,
        memory_root=tmp_path / "runtime" / "memory",
        skills_root=tmp_path / "runtime" / "skills",
        staging_root=tmp_path / "runtime" / "staged" / state.run_id,
    )
    register_hermes_tools(context, runtime)
    return registry, runtime


def test_registers_host_owned_tools_with_strict_schemas(tool_runtime):
    registry, _ = tool_runtime
    schemas = {
        item["function"]["name"]: item["function"]["parameters"]
        for item in registry.get_tool_schemas()
    }
    assert set(schemas) == {
        "assist_submit",
        "memory_propose",
        "reflection_submit",
        "skill_propose",
        "workspace_read",
        "workspace_write",
    }
    assert all(schema["additionalProperties"] is False for schema in schemas.values())


def test_workspace_read_is_bounded(tool_runtime):
    registry, runtime = tool_runtime
    (runtime.workspace / "large.txt").write_text("x" * 21_000, encoding="utf-8")

    result = registry.call("workspace_read", {"path": "large.txt"})

    assert len(result) < 21_000
    assert result.endswith("... output truncated ...")


@pytest.mark.parametrize(
    "path",
    ["../outside", "/tmp/outside", ".git/config", ".nano_hermes/private"],
)
def test_workspace_paths_reject_escape_and_internals(tool_runtime, path):
    registry, _ = tool_runtime
    with pytest.raises(ValueError):
        registry.call("workspace_read", {"path": path})


def test_workspace_read_rejects_outside_symlink(tool_runtime, tmp_path):
    registry, runtime = tool_runtime
    secret = tmp_path / "secret.txt"
    secret.write_text("private", encoding="utf-8")
    (runtime.workspace / "link.txt").symlink_to(secret)

    with pytest.raises(ValueError, match="escapes"):
        registry.call("workspace_read", {"path": "link.txt"})


def test_workspace_write_is_assist_only(tool_runtime):
    registry, runtime = tool_runtime
    registry.call("workspace_write", {"path": "notes/a.md", "content": "one"})
    assert (runtime.workspace / "notes" / "a.md").read_text(encoding="utf-8") == "one"
    runtime.state.phase = HermesPhase.REFLECT
    with pytest.raises(RuntimeError, match="requires assist phase"):
        registry.call("workspace_write", {"path": "b.md", "content": "two"})


def test_assist_submit_persists_response_and_enters_reflect(tool_runtime):
    registry, runtime = tool_runtime

    result = registry.call("assist_submit", {"answer": "Useful answer"})

    assert "reflection" in result
    assert runtime.state.response == "Useful answer"
    assert runtime.state.phase == HermesPhase.REFLECT
    assert runtime.store.load().phase == HermesPhase.REFLECT


def test_assist_submit_rejects_empty_or_different_persisted_answer(tool_runtime):
    registry, runtime = tool_runtime
    with pytest.raises(RuntimeError, match="answer is required"):
        registry.call("assist_submit", {"answer": " "})
    runtime.state.response = "first"
    with pytest.raises(RuntimeError, match="different answer"):
        registry.call("assist_submit", {"answer": "second"})


def test_memory_proposal_is_staged_without_active_write(tool_runtime):
    registry, runtime = tool_runtime
    runtime.state.phase = HermesPhase.REFLECT

    result = registry.call(
        "memory_propose",
        {
            "name": "user-preference",
            "content": "Prefer inspectable output.",
            "description": "Output preference",
            "type": "feedback",
        },
    )

    proposal = runtime.state.proposals[0]
    assert "staged memory proposal" in result
    assert proposal.kind == ProposalKind.MEMORY
    assert Path(proposal.staged_path).is_file()
    assert not (runtime.memory_root / "user-preference.md").exists()


def test_skill_proposal_captures_active_base_revision(tool_runtime):
    registry, runtime = tool_runtime
    runtime.state.phase = HermesPhase.REFLECT
    runtime.skills_root.mkdir(parents=True)
    target = runtime.skills_root / "review.md"
    target.write_text("old skill", encoding="utf-8")

    registry.call(
        "skill_propose",
        {
            "name": "review",
            "content": "new skill",
            "description": "Review",
            "trigger": "when reviewing",
        },
    )

    assert runtime.state.proposals[0].base_sha256
    assert target.read_text(encoding="utf-8") == "old skill"


def test_proposal_retry_is_idempotent_but_content_change_is_rejected(tool_runtime):
    registry, runtime = tool_runtime
    runtime.state.phase = HermesPhase.REFLECT
    arguments = {"name": "topic", "content": "same"}

    first = registry.call("memory_propose", arguments)
    second = registry.call("memory_propose", arguments)

    assert first == second
    assert len(runtime.state.proposals) == 1
    with pytest.raises(RuntimeError, match="different memory proposal"):
        registry.call("memory_propose", {"name": "topic", "content": "changed"})


def test_invalid_proposal_name_is_rejected_before_path_access(tool_runtime):
    registry, runtime = tool_runtime
    runtime.state.phase = HermesPhase.REFLECT
    with pytest.raises(ValueError, match="unsupported characters"):
        registry.call(
            "memory_propose",
            {"name": "../outside", "content": "unsafe"},
        )


def test_proposals_are_reflect_only(tool_runtime):
    registry, _ = tool_runtime
    with pytest.raises(RuntimeError, match="requires reflect phase"):
        registry.call("skill_propose", {"name": "skill", "content": "body"})


def test_reflection_submit_enters_host_review(tool_runtime):
    registry, runtime = tool_runtime
    runtime.state.phase = HermesPhase.REFLECT
    registry.call("memory_propose", {"name": "topic", "content": "durable"})

    result = registry.call("reflection_submit", {"summary": "one proposal"})

    assert result == "Reflection submitted with 1 proposal(s)"
    assert runtime.state.phase == HermesPhase.REVIEW
    assert runtime.state.reflection_summary == "one proposal"


def test_reflection_submit_requires_summary(tool_runtime):
    registry, runtime = tool_runtime
    runtime.state.phase = HermesPhase.REFLECT
    with pytest.raises(RuntimeError, match="summary is required"):
        registry.call("reflection_submit", {"summary": ""})
