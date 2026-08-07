import subprocess

import pytest

from nanoharness.components import DictToolRegistry
from nanoharness.extensions import ExtensionContext
from nanoharness.extensions.tasks import TaskBoard
from nanoharness.extensions.worktrees import WorktreeRegistry

from app.models import (
    CodexPhase,
    CodexRunState,
    DeliveryMode,
    DeliveryStatus,
    TrustedCommand,
)
from app.store import CodexRunStore
from app.tools import CodexToolRuntime, register_codex_tools


@pytest.fixture
def tool_runtime(tmp_path, git_repo, demo_job):
    state = CodexRunState(
        job_name=demo_job.name,
        job_fingerprint=demo_job.fingerprint(),
        objective=demo_job.objective,
        repository=str(git_repo),
        active_workspace=str(git_repo),
        phase=CodexPhase.EXECUTE,
    )
    store = CodexRunStore(tmp_path / "runtime" / "run.json")
    store.save(state)
    registry = DictToolRegistry()
    context = ExtensionContext(
        tools=registry,
        services={"tasks": TaskBoard()},
        capabilities=set(),
        metadata={},
    )
    runtime = CodexToolRuntime(demo_job, state, store, context, git_repo)
    register_codex_tools(context, runtime)
    return registry, runtime


def test_registers_complete_coding_surface_with_explicit_array_schemas(tool_runtime):
    registry, _ = tool_runtime

    schemas = {
        item["function"]["name"]: item["function"]["parameters"]
        for item in registry.get_tool_schemas()
    }

    assert set(schemas) == {
        "delivery_submit",
        "execution_finish",
        "plan_submit",
        "review_submit",
        "workspace_diff",
        "workspace_list",
        "workspace_patch",
        "workspace_read",
        "workspace_search",
        "workspace_status",
        "workspace_test",
        "workspace_write",
    }
    assert schemas["plan_submit"]["properties"]["steps"]["type"] == "array"
    assert schemas["review_submit"]["properties"]["findings"]["type"] == "array"
    assert all(schema["additionalProperties"] is False for schema in schemas.values())


def test_workspace_list_hides_host_internal_directories(tool_runtime, git_repo):
    registry, _ = tool_runtime
    (git_repo / ".nano_codex").mkdir()
    (git_repo / ".nano_codex" / "secret.txt").write_text("hidden", encoding="utf-8")
    (git_repo / "src").mkdir()
    (git_repo / "src" / "app.py").write_text("print('ok')\n", encoding="utf-8")

    result = registry.call("workspace_list", {"recursive": True})

    assert "src/app.py" in result
    assert ".git" not in result
    assert ".nano_codex" not in result


@pytest.mark.parametrize("path", ["../outside.txt", "/tmp/outside.txt", ".git/config", ".nano_codex/run.json"])
def test_workspace_tools_reject_escape_and_internal_paths(tool_runtime, path):
    registry, _ = tool_runtime

    with pytest.raises(ValueError):
        registry.call("workspace_read", {"path": path})


def test_workspace_read_rejects_symlink_into_internal_directory(tool_runtime, git_repo):
    registry, _ = tool_runtime
    (git_repo / "internal-link").symlink_to(git_repo / ".git", target_is_directory=True)

    with pytest.raises(ValueError, match="internal path"):
        registry.call("workspace_read", {"path": "internal-link/config"})


def test_workspace_search_skips_symlinks_outside_workspace(
    tool_runtime,
    git_repo,
    tmp_path,
):
    registry, _ = tool_runtime
    secret = tmp_path / "outside-secret.txt"
    secret.write_text("outside needle\n", encoding="utf-8")
    (git_repo / "linked-secret.txt").symlink_to(secret)

    assert registry.call("workspace_search", {"query": "outside needle"}) == (
        "No matches found."
    )


def test_workspace_read_truncates_large_output(tool_runtime, git_repo):
    registry, _ = tool_runtime
    (git_repo / "large.txt").write_text("x" * 21_000, encoding="utf-8")

    result = registry.call("workspace_read", {"path": "large.txt"})

    assert len(result) < 21_000
    assert result.endswith("... output truncated ...")


def test_workspace_search_is_literal_and_reports_line_numbers(tool_runtime, git_repo):
    registry, _ = tool_runtime
    (git_repo / "notes.txt").write_text("alpha.*beta\nalpha beta\n", encoding="utf-8")

    result = registry.call(
        "workspace_search",
        {"query": "alpha.*beta", "glob": "*.txt"},
    )

    assert result == "notes.txt:1:alpha.*beta"


def test_workspace_search_returns_clear_empty_result(tool_runtime):
    registry, _ = tool_runtime

    assert registry.call("workspace_search", {"query": "missing"}) == "No matches found."


def test_workspace_write_records_each_changed_path_once(tool_runtime, git_repo):
    registry, runtime = tool_runtime

    registry.call("workspace_write", {"path": "src/new.py", "content": "one\n"})
    registry.call("workspace_write", {"path": "src/new.py", "content": "two\n"})

    assert (git_repo / "src" / "new.py").read_text(encoding="utf-8") == "two\n"
    assert runtime.state.changed_files == ["src/new.py"]
    assert runtime.store.load().changed_files == ["src/new.py"]


def test_workspace_write_is_execute_only(tool_runtime):
    registry, runtime = tool_runtime
    runtime.state.phase = CodexPhase.REVIEW

    with pytest.raises(RuntimeError, match="requires execute phase"):
        registry.call("workspace_write", {"path": "new.txt", "content": "x"})


def test_workspace_patch_replaces_exact_expected_count(tool_runtime, git_repo):
    registry, runtime = tool_runtime
    target = git_repo / "README.md"
    target.write_text("old old\n", encoding="utf-8")

    result = registry.call(
        "workspace_patch",
        {
            "path": "README.md",
            "old_text": "old",
            "new_text": "new",
            "expected_replacements": 2,
        },
    )

    assert result == "patched README.md (2 replacement(s))"
    assert target.read_text(encoding="utf-8") == "new new\n"
    assert runtime.state.changed_files == ["README.md"]


def test_workspace_patch_fails_without_partial_edit(tool_runtime, git_repo):
    registry, _ = tool_runtime
    target = git_repo / "README.md"
    before = target.read_text(encoding="utf-8")

    with pytest.raises(RuntimeError, match=r"expected 2 replacement\(s\), found 1"):
        registry.call(
            "workspace_patch",
            {
                "path": "README.md",
                "old_text": "test",
                "new_text": "changed",
                "expected_replacements": 2,
            },
        )

    assert target.read_text(encoding="utf-8") == before


def test_workspace_status_and_diff_expose_git_state(tool_runtime):
    registry, _ = tool_runtime
    registry.call("workspace_write", {"path": "README.md", "content": "# changed\n"})

    assert "README.md" in registry.call("workspace_status", {})
    assert "+# changed" in registry.call("workspace_diff", {})


def test_workspace_test_runs_only_named_host_command(tool_runtime):
    registry, _ = tool_runtime

    assert "passed with no output" in registry.call("workspace_test", {"name": "diff-check"})


def test_workspace_test_rejects_unknown_command(tool_runtime):
    registry, _ = tool_runtime

    with pytest.raises(RuntimeError, match="unknown trusted command 'raw-shell'"):
        registry.call("workspace_test", {"name": "raw-shell"})


def test_workspace_test_surfaces_nonzero_exit_without_shell(tool_runtime):
    registry, runtime = tool_runtime
    runtime.job = runtime.job.model_copy(update={
        "commands": {
            "fail": TrustedCommand(
                argv=["git", "rev-parse", "--verify", "definitely-missing-ref"]
            )
        }
    })

    with pytest.raises(RuntimeError, match="Needed a single revision"):
        registry.call("workspace_test", {"name": "fail"})


def test_execution_finish_requires_a_controlled_change(tool_runtime):
    registry, _ = tool_runtime

    with pytest.raises(RuntimeError, match="without a workspace change"):
        registry.call("execution_finish", {"summary": "nothing"})


def test_execution_finish_rejects_a_no_op_write(tool_runtime, git_repo):
    registry, runtime = tool_runtime
    original = (git_repo / "README.md").read_text(encoding="utf-8")
    registry.call("workspace_write", {"path": "README.md", "content": original})

    with pytest.raises(RuntimeError, match="without an actual Git change"):
        registry.call("execution_finish", {"summary": "no-op"})

    assert runtime.state.phase == CodexPhase.EXECUTE


def test_execution_finish_completes_tasks_and_enters_review(tool_runtime):
    registry, runtime = tool_runtime
    board = runtime.context.services["tasks"]
    task = board.add(subject="edit", owner="nano-codex")
    runtime.state.step_task_ids = [task["id"]]
    registry.call("workspace_write", {"path": "result.txt", "content": "done\n"})

    result = registry.call("execution_finish", {"summary": "implemented"})

    assert result == "Execution complete with 1 changed file(s)"
    assert runtime.state.phase == CodexPhase.REVIEW
    assert board.get(task["id"])["status"].value == "completed"


def test_review_and_delivery_must_be_ordered(tool_runtime):
    registry, runtime = tool_runtime
    runtime.state.phase = CodexPhase.REVIEW

    with pytest.raises(RuntimeError, match="review_submit must run"):
        registry.call("delivery_submit", {"mode": "commit"})

    registry.call("review_submit", {"verdict": "pass", "findings": []})
    result = registry.call("delivery_submit", {"mode": "commit"})

    assert "queued" in result
    assert runtime.state.delivery_mode == DeliveryMode.COMMIT
    assert runtime.state.delivery_status == DeliveryStatus.PENDING


def test_delivery_rejects_disallowed_or_sourceless_modes(tool_runtime):
    registry, runtime = tool_runtime
    runtime.state.phase = CodexPhase.REVIEW
    runtime.state.agent_review = "pass"

    with pytest.raises(RuntimeError, match="is not allowed"):
        registry.call("delivery_submit", {"mode": "merge"})

    runtime.job = runtime.job.model_copy(update={
        "allowed_deliveries": [DeliveryMode.APPLY]
    })
    with pytest.raises(RuntimeError, match="requires a source repository"):
        registry.call("delivery_submit", {"mode": "apply"})


def test_plan_submit_persists_task_graph_and_worktree(tmp_path, git_repo, demo_job):
    state = CodexRunState(
        job_name=demo_job.name,
        job_fingerprint=demo_job.fingerprint(),
        objective=demo_job.objective,
        repository=str(git_repo),
    )
    store = CodexRunStore(tmp_path / "run.json")
    store.save(state)
    board = TaskBoard()
    worktrees = WorktreeRegistry(workspace_root=str(git_repo), task_board=board)
    registry = DictToolRegistry()
    context = ExtensionContext(
        tools=registry,
        services={"tasks": board, "worktrees": worktrees},
        capabilities=set(),
        metadata={},
    )
    runtime = CodexToolRuntime(demo_job, state, store, context, git_repo)
    register_codex_tools(context, runtime)

    result = registry.call("plan_submit", {"steps": ["inspect", "edit"]})

    assert "Accepted 2 plan steps" in result
    assert state.phase == CodexPhase.EXECUTE
    assert len(state.step_task_ids) == 2
    assert board.get(state.step_task_ids[1])["blockedBy"] == [state.step_task_ids[0]]
    assert state.active_workspace
    assert subprocess.run(
        ["git", "rev-parse", "--is-inside-work-tree"],
        cwd=state.active_workspace,
        capture_output=True,
        text=True,
    ).stdout.strip() == "true"
