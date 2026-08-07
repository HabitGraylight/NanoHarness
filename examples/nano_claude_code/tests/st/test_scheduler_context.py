"""NanoClaudeCode integration between ManagedContext and Scheduler."""

import tempfile
import time

from app.context import ManagedContext
from nanoharness.components.context import SimpleContextManager
from nanoharness.core.schema import AgentMessage
from nanoharness.extensions.scheduler import Scheduler


class TestManagedContextSchedulerDrain:
    def test_scheduler_notifications_injected(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            scheduler = Scheduler()
            scheduler.create("Quick fire", delay_seconds=1)
            time.sleep(2)
            scheduler._check_all()
            context = ManagedContext(
                inner=SimpleContextManager(system_prompt="test"),
                scratch_dir=tmpdir,
                scheduler=scheduler,
            )
            messages = context.get_full_context()
            scheduled = [
                item for item in messages if "Scheduled" in item.get("content", "")
            ]
            assert len(scheduled) == 1
            assert "Quick fire" in scheduled[0]["content"]
            scheduler.stop()

    def test_no_scheduler_no_error(self):
        context = ManagedContext(
            inner=SimpleContextManager(system_prompt="test"),
            scratch_dir="/tmp/test_no_sched",
        )
        context.add_message(AgentMessage(role="user", content="hi"))
        assert len(context.get_full_context()) >= 2
