"""Tests for TaskBoard — CRUD, status transitions, dependencies, is_ready, persistence."""

import sys
import os
import json
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, _ROOT)

import pytest

from app.task_system import TaskBoard, TaskStatus, is_ready, make_task


# ── TaskRecord & is_ready ──


class TestIsReady:
    def test_pending_no_blockers(self):
        task = make_task("Do thing")
        assert is_ready(task) is True

    def test_pending_with_blockers(self):
        task = make_task("Do thing", blockedBy=[1, 2])
        assert is_ready(task) is False

    def test_in_progress_not_ready(self):
        task = make_task("Do thing", status=TaskStatus.IN_PROGRESS)
        assert is_ready(task) is False

    def test_completed_not_ready(self):
        task = make_task("Do thing", status=TaskStatus.COMPLETED)
        assert is_ready(task) is False

    def test_deleted_not_ready(self):
        task = make_task("Do thing", status=TaskStatus.DELETED)
        assert is_ready(task) is False


# ── CRUD ──


class TestAdd:
    def test_basic_create(self):
        board = TaskBoard()
        task = board.add("Write parser")
        assert task["id"] == 1
        assert task["subject"] == "Write parser"
        assert task["status"] == TaskStatus.PENDING
        assert task["blockedBy"] == []
        assert task["blocks"] == []

    def test_auto_increment_id(self):
        board = TaskBoard()
        board.add("First")
        task2 = board.add("Second")
        assert task2["id"] == 2

    def test_with_description_and_owner(self):
        board = TaskBoard()
        task = board.add("Write tests", description="Unit tests for parser", owner="alice")
        assert task["description"] == "Unit tests for parser"
        assert task["owner"] == "alice"

    def test_invalid_dependency_raises(self):
        board = TaskBoard()
        with pytest.raises(KeyError, match="does not exist"):
            board.add("Write tests", blocked_by=[99])


class TestGet:
    def test_found(self):
        board = TaskBoard()
        board.add("Task A")
        task = board.get(1)
        assert task is not None
        assert task["subject"] == "Task A"

    def test_not_found(self):
        board = TaskBoard()
        assert board.get(99) is None


class TestUpdate:
    def test_update_subject(self):
        board = TaskBoard()
        board.add("Old subject")
        task = board.update(1, subject="New subject")
        assert task["subject"] == "New subject"

    def test_update_owner(self):
        board = TaskBoard()
        board.add("Task")
        task = board.update(1, owner="bob")
        assert task["owner"] == "bob"

    def test_update_nonexistent_raises(self):
        board = TaskBoard()
        with pytest.raises(KeyError):
            board.update(99, subject="nope")


# ── Status transitions ──


class TestStart:
    def test_pending_to_in_progress(self):
        board = TaskBoard()
        board.add("Task")
        task = board.start(1)
        assert task["status"] == TaskStatus.IN_PROGRESS

    def test_start_with_owner(self):
        board = TaskBoard()
        board.add("Task")
        task = board.start(1, owner="alice")
        assert task["status"] == TaskStatus.IN_PROGRESS
        assert task["owner"] == "alice"

    def test_cannot_start_completed(self):
        board = TaskBoard()
        board.add("Task")
        board.complete(1)
        with pytest.raises(ValueError, match="cannot start"):
            board.start(1)

    def test_cannot_start_deleted(self):
        board = TaskBoard()
        board.add("Task")
        board.delete(1)
        with pytest.raises(ValueError, match="cannot start"):
            board.start(1)


class TestComplete:
    def test_pending_to_completed(self):
        board = TaskBoard()
        board.add("Task")
        task = board.complete(1)
        assert task["status"] == TaskStatus.COMPLETED

    def test_in_progress_to_completed(self):
        board = TaskBoard()
        board.add("Task")
        board.start(1)
        task = board.complete(1)
        assert task["status"] == TaskStatus.COMPLETED

    def test_cannot_complete_deleted(self):
        board = TaskBoard()
        board.add("Task")
        board.delete(1)
        with pytest.raises(ValueError, match="cannot complete"):
            board.complete(1)


class TestDelete:
    def test_logical_delete(self):
        board = TaskBoard()
        board.add("Task")
        task = board.delete(1)
        assert task["status"] == TaskStatus.DELETED
        # Still in board
        assert board.get(1) is not None


# ── Dependencies ──


class TestDependencies:
    def test_blocked_task_not_ready(self):
        board = TaskBoard()
        board.add("Write parser")
        board.add("Write tests", blocked_by=[1])
        ready = board.ready()
        assert len(ready) == 1
        assert ready[0]["subject"] == "Write parser"

    def test_complete_unblocks_dependent(self):
        board = TaskBoard()
        board.add("Write parser")
        board.add("Write tests", blocked_by=[1])

        board.complete(1)

        ready = board.ready()
        assert len(ready) == 1
        assert ready[0]["subject"] == "Write tests"

    def test_chain_unblocking(self):
        board = TaskBoard()
        board.add("Design")         # 1
        board.add("Implement", blocked_by=[1])  # 2
        board.add("Test", blocked_by=[2])       # 3

        # Only Design is ready
        assert len(board.ready()) == 1

        board.complete(1)
        assert len(board.ready()) == 1
        assert board.ready()[0]["subject"] == "Implement"

        board.complete(2)
        assert len(board.ready()) == 1
        assert board.ready()[0]["subject"] == "Test"

    def test_reverse_links(self):
        board = TaskBoard()
        board.add("A")
        board.add("B", blocked_by=[1])
        task_a = board.get(1)
        assert 2 in task_a["blocks"]

    def test_delete_removes_from_blockedBy(self):
        board = TaskBoard()
        board.add("A")
        board.add("B", blocked_by=[1])

        board.delete(1)

        task_b = board.get(2)
        assert task_b["blockedBy"] == []
        assert is_ready(task_b) is True

    def test_update_add_blocked_by(self):
        board = TaskBoard()
        board.add("A")
        board.add("B")
        board.update(2, add_blocked_by=[1])

        task_b = board.get(2)
        assert 1 in task_b["blockedBy"]
        task_a = board.get(1)
        assert 2 in task_a["blocks"]

    def test_update_add_invalid_blocked_by(self):
        board = TaskBoard()
        board.add("A")
        with pytest.raises(KeyError):
            board.update(1, add_blocked_by=[99])


# ── Listing & filtering ──


class TestListFilter:
    def test_list_all(self):
        board = TaskBoard()
        board.add("A")
        board.add("B")
        assert len(board.list()) == 2

    def test_filter_by_status(self):
        board = TaskBoard()
        board.add("A")
        board.add("B")
        board.complete(1)
        pending = board.list(status=TaskStatus.PENDING)
        assert len(pending) == 1
        assert pending[0]["subject"] == "B"

    def test_filter_by_owner(self):
        board = TaskBoard()
        board.add("A", owner="alice")
        board.add("B", owner="bob")
        result = board.list(owner="alice")
        assert len(result) == 1
        assert result[0]["subject"] == "A"

    def test_empty_board(self):
        board = TaskBoard()
        assert board.list() == []


# ── Persistence ──


class TestPersistence:
    def test_save_and_load(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "tasks.json")

            # Create and save
            board1 = TaskBoard(persist_path=path)
            board1.add("Design")
            board1.add("Implement", blocked_by=[1])
            board1.complete(1)

            # Load from disk
            board2 = TaskBoard(persist_path=path)
            assert board2.get(1)["status"] == TaskStatus.COMPLETED
            assert board2.get(2)["subject"] == "Implement"
            assert board2.get(2)["blockedBy"] == []  # unblocked after complete
            assert board2._next_id == 3

    def test_no_persist_path(self):
        board = TaskBoard()
        board.add("Task")
        # No file created — no error
        assert board.get(1) is not None


# ── Summary ──


class TestSummary:
    def test_summary_output(self):
        board = TaskBoard()
        board.add("Design")
        board.add("Implement", blocked_by=[1])
        board.start(1, owner="alice")

        summary = board.summary()
        assert "Total: 2" in summary
        assert "pending: 1" in summary
        assert "in_progress: 1" in summary
        assert "Ready" not in summary  # no ready tasks (1 is in_progress, 2 is blocked)
        assert "alice" in summary

    def test_summary_with_ready(self):
        board = TaskBoard()
        board.add("Task A")
        board.add("Task B")

        summary = board.summary()
        assert "Ready" in summary
        assert "Task A" in summary
        assert "Task B" in summary
