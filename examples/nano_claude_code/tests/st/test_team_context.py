"""NanoClaudeCode ManagedContext integration for Team notifications."""

import tempfile
import time
from pathlib import Path

from app.context import ManagedContext
from nanoharness.components.context import SimpleContextManager
from nanoharness.components.tools import DictToolRegistry
from nanoharness.core.schema import LLMResponse
from nanoharness.extensions.teams import TeammateManager


class FakeLLM:
    def chat(self, messages, tools=None):
        return LLMResponse(content="Done.")


def test_team_notifications_can_be_drained_by_managed_context():
    with tempfile.TemporaryDirectory() as tmpdir:
        manager = TeammateManager(
            llm_client=FakeLLM(),
            registry=DictToolRegistry(),
            workspace_root=tmpdir,
            team_dir=tmpdir,
        )
        ManagedContext(
            inner=SimpleContextManager(system_prompt="test"),
            scratch_dir=str(Path(tmpdir) / "scratch"),
            teammate_manager=manager,
        )
        manager.spawn("worker")
        manager.send("worker", "Quick task")
        notifications = []
        deadline = time.time() + 15
        while time.time() < deadline and not notifications:
            notifications = manager.drain()
            time.sleep(0.1)
        manager.shutdown("worker")
        assert notifications
