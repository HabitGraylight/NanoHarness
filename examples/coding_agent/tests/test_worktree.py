"""Tests for WorktreeRegistry — create, enter, run, closeout, event logging, task binding."""

import sys
import os
import json
import subprocess
import tempfile
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, _ROOT)

import pytest

from app.worktree import WorktreeRegistry, register_worktree_tools
from app.task_system import TaskBoard, TaskStatus
from app.dispatch import DispatchRegistry


# ── Helpers ──


@pytest.fixture
def tmp_git_repo(tmp_path):
    """Create a temporary git repo with an initial commit."""
    subprocess.run(["git", "init"], cwd=str(tmp_path), capture_output=True, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=str(tmp_path), capture_output=True, check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=str(tmp_path), capture_output=True, check=True,
    )
    # Need at least one commit for worktree add to work
    readme = tmp_path / "README.md"
    readme.write_text("# test")
    subprocess.run(["git", "add", "."], cwd=str(tmp_path), capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=str(tmp_path), capture_output=True, check=True,
    )
    return tmp_path


@pytest.fixture
def wt(tmp_git_repo):
    """WorktreeRegistry on a temp git repo."""
    return WorktreeRegistry(workspace_root=str(tmp_git_repo))


@pytest.fixture
def wt_with_board(tmp_git_repo):
    """WorktreeRegistry + TaskBoard on a temp git repo."""
    board = TaskBoard()
    registry = WorktreeRegistry(workspace_root=str(tmp_git_repo), task_board=board)
    return registry, board


# ── TestWorktreeRegistry ──


class TestWorktreeRegistry:

    def test_create_adds_git_worktree(self, wt, tmp_git_repo):
        record = wt.create("auth-refactor", task_id=1)

        assert record["name"] == "auth-refactor"
        assert record["branch"] == "wt/auth-refactor"
        assert record["status"] == "active"
        assert record["task_id"] == 1

        # Directory should exist
        wt_path = tmp_git_repo / ".worktrees" / "auth-refactor"
        assert wt_path.is_dir()

    def test_create_writes_index(self, wt):
        wt.create("feat-x", task_id=2)

        with open(wt._index_path) as f:
            data = json.load(f)

        names = [r["name"] for r in data["worktrees"]]
        assert "feat-x" in names

    def test_create_emits_event(self, wt):
        wt.create("feat-y", task_id=3)

        with open(wt._events_path) as f:
            events = [json.loads(line) for line in f if line.strip()]

        assert any(e["event"] == "worktree.create" and e["name"] == "feat-y" for e in events)

    def test_create_duplicate_raises(self, wt):
        wt.create("dup", task_id=1)
        with pytest.raises(ValueError, match="already exists"):
            wt.create("dup", task_id=2)

    def test_enter_updates_tracking(self, wt):
        wt.create("enter-test", task_id=1)
        before = time.time()
        record = wt.enter("enter-test")
        after = time.time()

        assert record["last_entered_at"] is not None
        assert before <= record["last_entered_at"] <= after

    def test_enter_emits_event(self, wt):
        wt.create("enter-test", task_id=1)
        wt.enter("enter-test")

        with open(wt._events_path) as f:
            events = [json.loads(line) for line in f if line.strip()]

        assert any(e["event"] == "worktree.enter" for e in events)

    def test_enter_nonexistent_raises(self, wt):
        with pytest.raises(KeyError, match="not found"):
            wt.enter("no-such")

    def test_run_executes_in_worktree_cwd(self, wt, tmp_git_repo):
        wt.create("run-test", task_id=1)
        exit_code, output = wt.run("run-test", "pwd")

        wt_path = str(tmp_git_repo / ".worktrees" / "run-test")
        assert wt_path in output.strip()
        assert exit_code == 0

    def test_run_updates_tracking(self, wt):
        wt.create("run-test", task_id=1)
        wt.run("run-test", "echo hello")

        record = wt.get("run-test")
        assert record["last_command_at"] is not None
        assert "echo hello" in record["last_command_preview"]

    def test_run_nonexistent_raises(self, wt):
        with pytest.raises(KeyError):
            wt.run("no-such", "echo hi")

    def test_get_returns_none_for_missing(self, wt):
        assert wt.get("missing") is None

    def test_list_all(self, wt):
        wt.create("a", task_id=1)
        wt.create("b", task_id=2)

        records = wt.list()
        names = {r["name"] for r in records}
        assert names == {"a", "b"}

    def test_list_filtered_by_status(self, wt):
        wt.create("active-wt", task_id=1)
        wt.create("kept-wt", task_id=2)
        # Manually set one to kept
        wt._index["kept-wt"]["status"] = "kept"
        wt._save_index()

        kept = wt.list(status="kept")
        assert len(kept) == 1
        assert kept[0]["name"] == "kept-wt"


# ── TestWorktreeCloseout ──


class TestWorktreeCloseout:

    def test_closeout_keep(self, wt, tmp_git_repo):
        wt.create("keep-test", task_id=1)
        record = wt.closeout("keep-test", action="keep", reason="Need review")

        assert record["status"] == "kept"
        assert record["closeout"]["action"] == "keep"
        assert record["closeout"]["reason"] == "Need review"

        # Directory should still exist
        wt_path = tmp_git_repo / ".worktrees" / "keep-test"
        assert wt_path.is_dir()

    def test_closeout_remove(self, wt, tmp_git_repo):
        wt.create("rm-test", task_id=1)
        record = wt.closeout("rm-test", action="remove", reason="Done")

        assert record["status"] == "removed"
        assert record["closeout"]["action"] == "remove"

        # Directory should be gone
        wt_path = tmp_git_repo / ".worktrees" / "rm-test"
        assert not wt_path.exists()

    def test_closeout_emits_event(self, wt):
        wt.create("event-test", task_id=1)
        wt.closeout("event-test", action="keep", reason="Testing")

        with open(wt._events_path) as f:
            events = [json.loads(line) for line in f if line.strip()]

        assert any(
            e["event"] == "worktree.closeout.keep" and e["reason"] == "Testing"
            for e in events
        )

    def test_closeout_invalid_action_raises(self, wt):
        wt.create("bad-action", task_id=1)
        with pytest.raises(ValueError, match="action must be"):
            wt.closeout("bad-action", action="explode")

    def test_closeout_with_complete_task(self, wt_with_board):
        wt, board = wt_with_board
        task = board.add("Test task")
        wt.create("task-wt", task_id=task["id"])

        wt.closeout("task-wt", action="remove", reason="Done", complete_task=True)

        updated = board.get(task["id"])
        assert updated["status"] == TaskStatus.COMPLETED


# ── TestTaskWorktreeBinding ──


class TestTaskWorktreeBinding:

    def test_create_auto_binds_task(self, wt_with_board):
        wt, board = wt_with_board
        task = board.add("Refactor auth")
        wt.create("auth-refactor", task_id=task["id"])

        updated = board.get(task["id"])
        assert updated["worktree"] == "auth-refactor"
        assert updated["worktree_state"] == "active"
        assert updated["last_worktree"] == "auth-refactor"
        # Pending task should auto-start when bound
        assert updated["status"] == TaskStatus.IN_PROGRESS

    def test_closeout_unbinds_task(self, wt_with_board):
        wt, board = wt_with_board
        task = board.add("Fix bug")
        wt.create("bugfix-wt", task_id=task["id"])

        wt.closeout("bugfix-wt", action="keep", reason="Needs review")

        updated = board.get(task["id"])
        assert updated["worktree"] is None
        assert updated["worktree_state"] == "kept"
        assert updated["closeout"]["action"] == "keep"

    def test_task_create_includes_worktree_fields(self):
        board = TaskBoard()
        task = board.add("Test task")

        assert task["worktree"] is None
        assert task["worktree_state"] == "unbound"
        assert task["last_worktree"] is None
        assert task["closeout"] is None

    def test_bind_in_progress_task_stays_in_progress(self):
        board = TaskBoard()
        task = board.add("Test task")
        board.start(task["id"])

        board.bind_worktree(task["id"], "some-wt")

        updated = board.get(task["id"])
        assert updated["status"] == TaskStatus.IN_PROGRESS  # no double-transition
        assert updated["worktree"] == "some-wt"


# ── TestToolRegistration ──


class TestToolRegistration:

    def test_worktree_tools_registered(self):
        registry = DispatchRegistry(workspace_root="/tmp")
        wt = WorktreeRegistry(workspace_root="/tmp")
        register_worktree_tools(registry=registry, wt_registry=wt)

        schemas = registry.schemas
        assert "worktree_create" in schemas
        assert "worktree_enter" in schemas
        assert "worktree_run" in schemas
        assert "worktree_closeout" in schemas
        assert "worktree_list" in schemas

    def test_worktree_list_empty(self):
        registry = DispatchRegistry(workspace_root="/tmp")
        wt = WorktreeRegistry(workspace_root="/tmp")
        register_worktree_tools(registry=registry, wt_registry=wt)

        result = registry.call("worktree_list", {})
        assert "No worktrees" in result
