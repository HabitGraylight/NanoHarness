"""Tests for BackgroundExecutor and ManagedContext notification draining."""

import sys
import os
import time
import tempfile


import pytest

from nanoharness.components.tools import DictToolRegistry
from nanoharness.extensions.background import BackgroundExecutor, register_background_tools
from nanoharness.extensions.background.executor import _MAX_PREVIEW_LINES


# ── Helpers ──

def _wait_for_task(bg, task_id, timeout=10):
    """Poll until a background task finishes or timeout."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        result = bg.poll(task_id)
        if result and result["status"] != "running":
            return result
        time.sleep(0.1)
    raise TimeoutError(f"Task {task_id} did not complete in {timeout}s")


# ── BackgroundExecutor ──


class TestBackgroundRun:
    def test_returns_task_id_immediately(self):
        bg = BackgroundExecutor("/tmp")
        task_id = bg.run("echo hello")
        assert isinstance(task_id, int)
        assert task_id > 0

    def test_auto_increment_ids(self):
        bg = BackgroundExecutor("/tmp")
        id1 = bg.run("echo 1")
        id2 = bg.run("echo 2")
        assert id2 == id1 + 1

    def test_max_concurrent_limit(self):
        bg = BackgroundExecutor("/tmp", max_concurrent=2)
        try:
            bg.run("sleep 5")
            bg.run("sleep 5")
            with pytest.raises(RuntimeError, match="Too many"):
                bg.run("echo fail")
        finally:
            bg.close()

    def test_empty_command_rejected_by_tool(self):
        """The tool handler validates command, not the executor itself."""
        bg = BackgroundExecutor("/tmp")
        registry = DictToolRegistry()
        register_background_tools(registry, bg)

        with pytest.raises(RuntimeError, match="command is required"):
            registry.call("background_run", {"command": ""})


class TestBackgroundDrain:
    def test_drain_completed_task(self):
        bg = BackgroundExecutor("/tmp")
        bg.run("echo hello")
        # Wait for completion
        time.sleep(1)
        notifications = bg.drain()
        assert len(notifications) == 1
        assert notifications[0]["status"] == "completed"
        assert "hello" in notifications[0]["message"]

    def test_drain_returns_empty_when_nothing_done(self):
        bg = BackgroundExecutor("/tmp")
        assert bg.drain() == []

    def test_drain_is_nonblocking(self):
        bg = BackgroundExecutor("/tmp")
        bg.run("sleep 10")
        # Immediately drain — task still running, no notifications yet
        assert bg.drain() == []
        bg.close()

    def test_drain_multiple_tasks(self):
        bg = BackgroundExecutor("/tmp")
        bg.run("echo a")
        bg.run("echo b")
        time.sleep(1)
        notifications = bg.drain()
        assert len(notifications) == 2

    def test_drain_consumes_queue(self):
        """Second drain returns nothing if no new completions."""
        bg = BackgroundExecutor("/tmp")
        bg.run("echo once")
        time.sleep(1)
        assert len(bg.drain()) == 1
        assert len(bg.drain()) == 0


class TestBackgroundPoll:
    def test_poll_running(self):
        bg = BackgroundExecutor("/tmp")
        task_id = bg.run("sleep 5")
        result = bg.poll(task_id)
        assert result is not None
        assert result["status"] == "running"
        bg.close()

    def test_poll_completed(self):
        bg = BackgroundExecutor("/tmp")
        task_id = bg.run("echo done")
        _wait_for_task(bg, task_id)
        result = bg.poll(task_id)
        assert result["status"] == "completed"
        assert result["exit_code"] == 0

    def test_poll_nonexistent(self):
        bg = BackgroundExecutor("/tmp")
        assert bg.poll(999) is None

    def test_poll_failed_command(self):
        bg = BackgroundExecutor("/tmp")
        task_id = bg.run("exit 1")
        _wait_for_task(bg, task_id)
        result = bg.poll(task_id)
        assert result["status"] == "failed"
        assert result["exit_code"] == 1


class TestBackgroundTimeout:
    def test_timeout_sets_status(self):
        bg = BackgroundExecutor("/tmp")
        task_id = bg.run("sleep 60", timeout=1)
        _wait_for_task(bg, task_id, timeout=5)
        result = bg.poll(task_id)
        assert result["status"] == "timeout"


class TestBackgroundOutput:
    def test_stdout_captured(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            bg = BackgroundExecutor("/tmp", scratch_dir=tmpdir)
            task_id = bg.run("echo 'hello world'")
            _wait_for_task(bg, task_id)
            time.sleep(0.2)  # let drain queue populate
            notifications = bg.drain()
            assert len(notifications) == 1
            assert "hello world" in notifications[0]["message"]
            assert notifications[0]["exit_code"] == 0
            assert notifications[0]["started_at"] is not None
            assert notifications[0]["finished_at"] is not None
            assert notifications[0]["log_path"].endswith(f"bg_{task_id}.log")

    def test_log_file_saved(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            bg = BackgroundExecutor("/tmp", scratch_dir=tmpdir)
            task_id = bg.run("echo 'saved output'")
            _wait_for_task(bg, task_id)
            time.sleep(0.2)
            # Check log file exists
            log_path = os.path.join(tmpdir, f"bg_{task_id}.log")
            assert os.path.exists(log_path)
            with open(log_path) as f:
                content = f.read()
            assert "saved output" in content

    def test_long_output_truncated(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            bg = BackgroundExecutor("/tmp", scratch_dir=tmpdir)
            # Generate 50 lines of output
            task_id = bg.run("for i in $(seq 1 50); do echo \"line $i\"; done")
            _wait_for_task(bg, task_id)
            time.sleep(0.2)
            notifications = bg.drain()
            msg = notifications[0]["message"]
            # Notification should be truncated
            assert f"last {_MAX_PREVIEW_LINES} lines" in msg
            assert "[Full output:" in msg
