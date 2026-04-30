"""Tests for BackgroundExecutor and ManagedContext notification draining."""

import sys
import os
import time
import tempfile


import pytest

from app.background import BackgroundExecutor, _MAX_PREVIEW_LINES
from app.context import ManagedContext
from nanoharness.components.context.simple_context import SimpleContextManager
from nanoharness.core.schema import AgentMessage


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
        bg.run("sleep 5")
        bg.run("sleep 5")
        with pytest.raises(RuntimeError, match="Too many"):
            bg.run("echo fail")

    def test_empty_command_rejected_by_tool(self):
        """The tool handler validates command, not the executor itself."""
        from app.dispatch import DispatchRegistry
        bg = BackgroundExecutor("/tmp")
        registry = DispatchRegistry(workspace_root="/tmp")
        from app.background import register_background_tools
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


# ── ManagedContext integration ──


class TestManagedContextDrain:
    def test_notifications_injected_into_context(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            bg = BackgroundExecutor(tmpdir, scratch_dir=tmpdir)
            context = ManagedContext(
                inner=SimpleContextManager(system_prompt="You are helpful."),
                scratch_dir=tmpdir,
                bg_executor=bg,
            )

            # Run a background task
            bg.run("echo done")
            time.sleep(1)

            # get_full_context should drain and inject notification
            messages = context.get_full_context()
            # Find the notification message
            notification_msgs = [m for m in messages if "Background" in m.get("content", "")]
            assert len(notification_msgs) == 1
            assert "done" in notification_msgs[0]["content"]

    def test_no_bg_executor_no_error(self):
        """get_full_context works fine without bg_executor."""
        context = ManagedContext(
            inner=SimpleContextManager(system_prompt="test"),
            scratch_dir="/tmp/test_no_bg",
        )
        context.add_message(AgentMessage(role="user", content="hi"))
        messages = context.get_full_context()
        assert len(messages) >= 2  # system + user

    def test_drain_consumed_on_second_call(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            bg = BackgroundExecutor(tmpdir, scratch_dir=tmpdir)
            context = ManagedContext(
                inner=SimpleContextManager(system_prompt="test"),
                scratch_dir=tmpdir,
                bg_executor=bg,
            )

            bg.run("echo once")
            time.sleep(1)

            msgs1 = context.get_full_context()
            bg_msgs1 = [m for m in msgs1 if "Background" in m.get("content", "")]
            assert len(bg_msgs1) == 1

            msgs2 = context.get_full_context()
            bg_msgs2 = [m for m in msgs2 if "Background" in m.get("content", "")]
            # The old notification is still in context (it was added as a message)
            # But no NEW notification should appear
            assert len(bg_msgs2) == len(bg_msgs1)
