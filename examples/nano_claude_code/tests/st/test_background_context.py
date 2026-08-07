"""NanoClaudeCode integration between ManagedContext and Background."""

import tempfile
import time

from app.context import ManagedContext
from nanoharness.components.context import SimpleContextManager
from nanoharness.core.schema import AgentMessage
from nanoharness.extensions.background import BackgroundExecutor


class TestManagedContextDrain:
    def test_notifications_injected_into_context(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            bg = BackgroundExecutor(tmpdir, scratch_dir=tmpdir)
            context = ManagedContext(
                inner=SimpleContextManager(system_prompt="You are helpful."),
                scratch_dir=tmpdir,
                bg_executor=bg,
            )
            bg.run("echo done")
            time.sleep(1)
            messages = context.get_full_context()
            notifications = [
                item for item in messages if "Background" in item.get("content", "")
            ]
            assert len(notifications) == 1
            assert "done" in notifications[0]["content"]

    def test_no_bg_executor_no_error(self):
        context = ManagedContext(
            inner=SimpleContextManager(system_prompt="test"),
            scratch_dir="/tmp/test_no_bg",
        )
        context.add_message(AgentMessage(role="user", content="hi"))
        assert len(context.get_full_context()) >= 2

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
            first = context.get_full_context()
            first_count = sum(
                "Background" in item.get("content", "") for item in first
            )
            second = context.get_full_context()
            second_count = sum(
                "Background" in item.get("content", "") for item in second
            )
            assert first_count == second_count == 1
