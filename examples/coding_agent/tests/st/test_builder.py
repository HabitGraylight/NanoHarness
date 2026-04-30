"""ST for build_coding_engine — full engine assembly wiring."""

import os
import tempfile

import pytest

from nanoharness.core.engine import NanoEngine
from nanoharness.core.base import HookStage
from nanoharness.core.schema import AgentMessage


class TestBuilderReturnsEngine:
    """build_coding_engine returns a fully wired NanoEngine."""

    def test_builder_returns_engine(self, monkeypatch, tmp_path):
        """Engine is a NanoEngine instance with all components wired."""
        # Monkey-patch OpenAI adapter to avoid real API calls
        from unittest.mock import MagicMock
        from app import builder as builder_mod

        mock_adapter = MagicMock()
        mock_adapter.chat.return_value = MagicMock(content="ok", tool_calls=None)

        original_adapter = builder_mod.OpenAIAdapter

        def fake_adapter(*args, **kwargs):
            return mock_adapter

        monkeypatch.setattr(builder_mod, "OpenAIAdapter", fake_adapter)

        # Point sandbox to tmp_path to avoid writing to real sandbox
        monkeypatch.setattr(builder_mod, "SANDBOX", str(tmp_path / "sandbox"))

        engine = builder_mod.build_coding_engine(
            api_key="fake-key",
            model="test-model",
        )
        assert isinstance(engine, NanoEngine)
        assert engine.llm is not None
        assert engine.tools is not None
        assert engine.context is not None
        assert engine.max_steps == 20

    def test_builder_has_tools(self, monkeypatch, tmp_path):
        """Engine has tools registered (file_read, search_code, etc.)."""
        from unittest.mock import MagicMock
        from app import builder as builder_mod

        mock_adapter = MagicMock()
        mock_adapter.chat.return_value = MagicMock(content="ok", tool_calls=None)
        monkeypatch.setattr(builder_mod, "OpenAIAdapter", lambda *a, **kw: mock_adapter)
        monkeypatch.setattr(builder_mod, "SANDBOX", str(tmp_path / "sandbox"))

        engine = builder_mod.build_coding_engine(api_key="fake-key")

        # Should have at least the shell-script tools and dispatch tools
        schemas = engine.tools.get_tool_schemas()
        tool_names = [s["function"]["name"] for s in schemas]
        assert len(tool_names) > 5
        # Core tools that should always be present
        assert "skill" in tool_names
        assert "task" in tool_names


class TestWireTaskAwareness:
    """Tests for _wire_task_awareness hook."""

    def test_wire_task_awareness_injects_summary(self, tmp_path):
        """When tasks exist, a summary is injected into context."""
        from app.builder import _wire_task_awareness
        from app.task_system import TaskBoard
        from app.context import ManagedContext
        from nanoharness.components.context.simple_context import SimpleContextManager
        from nanoharness.components.hooks.simple_hooks import SimpleHookManager

        hooks = SimpleHookManager()
        board = TaskBoard()
        scratch = str(tmp_path / "scratch")
        context = ManagedContext(
            inner=SimpleContextManager(system_prompt="test"),
            scratch_dir=scratch,
        )

        _wire_task_awareness(hooks, board, context)

        # Add a task to the board
        board.add("Write tests")

        # Trigger the hook
        hooks.trigger(HookStage.ON_TASK_START, "Do something")

        # Check that task summary was injected
        task_msgs = [m for m in context._messages if "Task Board" in m.content]
        assert len(task_msgs) == 1
        assert "Write tests" in task_msgs[0].content

    def test_wire_task_awareness_no_active_tasks(self, tmp_path):
        """When no tasks exist, no summary is injected."""
        from app.builder import _wire_task_awareness
        from app.task_system import TaskBoard
        from app.context import ManagedContext
        from nanoharness.components.context.simple_context import SimpleContextManager
        from nanoharness.components.hooks.simple_hooks import SimpleHookManager

        hooks = SimpleHookManager()
        board = TaskBoard()
        scratch = str(tmp_path / "scratch")
        context = ManagedContext(
            inner=SimpleContextManager(system_prompt="test"),
            scratch_dir=scratch,
        )

        _wire_task_awareness(hooks, board, context)

        # No tasks added
        initial_count = len(context._messages)

        # Trigger the hook
        hooks.trigger(HookStage.ON_TASK_START, "Do something")

        # No task summary injected
        assert len(context._messages) == initial_count
