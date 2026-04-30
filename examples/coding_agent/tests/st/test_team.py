"""ST tests for TeammateManager — teammate loop, ManagedContext integration, idle phase, work/idle cycle, autonomous claiming."""

import os
import time
import tempfile

import pytest

from app.team import (
    TeammateManager,
    RequestTracker,
    _load_roster,
    _save_roster,
    _roster_member,
    _inbox_path,
    _send_to_inbox,
    _read_inbox,
    _make_envelope,
    _make_protocol_envelope,
    _make_system_message,
    register_team_tools,
)
from app.dispatch import DispatchRegistry
from app.task_system import TaskBoard, is_claimable, is_ready


# ── Helpers ──


class FakeLLM:
    """Fake LLM that returns a canned response with no tool calls."""

    def __init__(self, response_text="Task complete."):
        self._response = response_text
        self.calls = []

    def chat(self, messages, tools=None):
        self.calls.append({"messages": messages, "tools": tools})
        from nanoharness.core.schema import LLMResponse
        return LLMResponse(content=self._response, tool_calls=None)


class FakeLLMWithTools:
    """Fake LLM that calls a tool on first turn, then responds."""

    def __init__(self, tool_name="file_read", tool_args=None, final_response="Done after tool."):
        from nanoharness.core.schema import ToolCall
        self._tool_call = ToolCall(name=tool_name, arguments=tool_args or {"path": "/tmp/test.txt"})
        self._final = final_response
        self.turn = 0

    def chat(self, messages, tools=None):
        from nanoharness.core.schema import LLMResponse
        self.turn += 1
        if self.turn == 1:
            return LLMResponse(content="", tool_calls=[self._tool_call])
        return LLMResponse(content=self._final, tool_calls=None)


def _make_registry(workspace_root="/tmp"):
    """Create a registry with a dummy read-only tool."""
    reg = DispatchRegistry(workspace_root=workspace_root)

    def fake_file_read(path="/tmp"):
        return f"Contents of {path}"

    from app.dispatch import inprocess_handler
    reg.register(
        name="file_read",
        handler=inprocess_handler(fake_file_read),
        schema={
            "type": "function",
            "function": {
                "name": "file_read",
                "description": "Read a file",
                "parameters": {
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                },
            },
        },
    )
    return reg


# ── Teammate loop integration ──


class TestTeammateLoop:
    def test_mate_processes_inbox_and_responds(self):
        """Spawn a teammate, send it a message, wait for response via drain."""
        with tempfile.TemporaryDirectory() as tmpdir:
            fake_llm = FakeLLM(response_text="I checked the files. All looks good.")
            tm = TeammateManager(
                llm_client=fake_llm,
                registry=_make_registry(),
                workspace_root=tmpdir,
                team_dir=tmpdir,
            )
            tm.spawn("researcher", role="research specialist")

            # Send a task
            tm.send("researcher", "Review the auth module")

            # Wait for the mate to process (inbox check interval + LLM call)
            deadline = time.time() + 15
            notifications = []
            while time.time() < deadline:
                notifications = tm.drain()
                if notifications:
                    break
                time.sleep(0.5)

            tm.shutdown("researcher")
            assert len(notifications) == 1
            assert "researcher" in notifications[0]["from"]
            assert "All looks good" in notifications[0]["message"]

    def test_mate_with_tool_call(self):
        """Teammate uses a tool then responds."""
        with tempfile.TemporaryDirectory() as tmpdir:
            fake_llm = FakeLLMWithTools(
                tool_name="file_read",
                tool_args={"path": "/tmp/test.txt"},
                final_response="Found the answer in the file.",
            )
            tm = TeammateManager(
                llm_client=fake_llm,
                registry=_make_registry(),
                workspace_root=tmpdir,
                team_dir=tmpdir,
            )
            tm.spawn("analyst")

            tm.send("analyst", "Read the test file")

            deadline = time.time() + 15
            notifications = []
            while time.time() < deadline:
                notifications = tm.drain()
                if notifications:
                    break
                time.sleep(0.5)

            tm.shutdown("analyst")
            assert len(notifications) == 1
            assert "Found the answer" in notifications[0]["message"]

    def test_multiple_messages_batched(self):
        """Send multiple messages — they get batched into one LLM call."""
        with tempfile.TemporaryDirectory() as tmpdir:
            fake_llm = FakeLLM(response_text="Combined response.")
            tm = TeammateManager(
                llm_client=fake_llm,
                registry=_make_registry(),
                workspace_root=tmpdir,
                team_dir=tmpdir,
            )
            tm.spawn("worker")

            tm.send("worker", "Task A")
            tm.send("worker", "Task B")

            deadline = time.time() + 15
            notifications = []
            while time.time() < deadline:
                notifications = tm.drain()
                if notifications:
                    break
                time.sleep(0.5)

            tm.shutdown("worker")
            assert len(notifications) >= 1
            # Both messages should be in the conversation
            last_call = fake_llm.calls[-1]
            user_contents = [m["content"] for m in last_call["messages"] if m["role"] == "user"]
            assert "Task A" in user_contents
            assert "Task B" in user_contents


# ── ManagedContext integration ──


class TestManagedContextIntegration:
    def test_drain_injects_into_context(self):
        """Simulate what ManagedContext will do with tm.drain()."""
        with tempfile.TemporaryDirectory() as tmpdir:
            from app.context import ManagedContext
            from nanoharness.components.context.simple_context import SimpleContextManager
            from nanoharness.core.schema import AgentMessage

            fake_llm = FakeLLM(response_text="Done.")
            tm = TeammateManager(
                llm_client=fake_llm,
                registry=_make_registry(),
                workspace_root=tmpdir,
                team_dir=tmpdir,
            )
            context = ManagedContext(
                inner=SimpleContextManager(system_prompt="test"),
                scratch_dir=os.path.join(tmpdir, "scratch"),
                teammate_manager=tm,  # Not wired yet — will fail if not added
            )

            tm.spawn("worker")
            tm.send("worker", "Quick task")

            deadline = time.time() + 15
            while time.time() < deadline:
                notifs = tm.drain()
                if notifs:
                    break
                time.sleep(0.5)

            tm.shutdown("worker")
            # Verify drain produced something
            assert len(notifs) >= 1


# ── Idle phase ──


class TestIdlePhase:
    def test_idle_picks_up_inbox_message(self):
        """During idle phase, inbox message triggers work."""
        with tempfile.TemporaryDirectory() as tmpdir:
            fake_llm = FakeLLM(response_text="Done.")
            tm = TeammateManager(
                llm_client=fake_llm,
                registry=_make_registry(tmpdir),
                workspace_root=tmpdir,
                team_dir=tmpdir,
            )
            tm.spawn("worker")
            tm.send("worker", "Do something")

            deadline = time.time() + 20
            notifications = []
            while time.time() < deadline:
                notifications = tm.drain()
                if notifications:
                    break
                time.sleep(0.5)

            tm.shutdown("worker")
            assert len(notifications) >= 1

    def test_idle_claims_task_from_board(self):
        """Idle phase scans task board and claims a task."""
        with tempfile.TemporaryDirectory() as tmpdir:
            fake_llm = FakeLLM(response_text="Task complete.")
            board = TaskBoard(persist_path=os.path.join(tmpdir, "tasks.json"))
            board.add("Autonomous task", description="Do it autonomously")

            tm = TeammateManager(
                llm_client=fake_llm,
                registry=_make_registry(tmpdir),
                workspace_root=tmpdir,
                team_dir=tmpdir,
                task_board=board,
            )
            tm.spawn("worker", role="assistant")

            deadline = time.time() + 30
            notifications = []
            while time.time() < deadline:
                notifications = tm.drain()
                if notifications:
                    break
                time.sleep(0.5)

            tm.shutdown("worker")
            assert len(notifications) >= 1
            assert "auto-claimed" in notifications[0]["message"]
            task = board.get(1)
            assert task["owner"] == "worker"
            assert task["status"] == "in_progress"

    def test_idle_with_nothing_exits_gracefully(self):
        """Idle phase with no inbox and no task board just waits."""
        with tempfile.TemporaryDirectory() as tmpdir:
            fake_llm = FakeLLM(response_text="Nothing to do.")
            tm = TeammateManager(
                llm_client=fake_llm,
                registry=_make_registry(tmpdir),
                workspace_root=tmpdir,
                team_dir=tmpdir,
            )
            tm.spawn("idle_worker")
            time.sleep(2)
            tm.shutdown("idle_worker")
            # Should not crash


# ── Work/idle cycle ──


class TestWorkIdleCycle:
    def test_full_cycle_inbox_then_task(self):
        """Teammate processes inbox, then claims task from board."""
        with tempfile.TemporaryDirectory() as tmpdir:
            fake_llm = FakeLLM(response_text="Completed.")
            board = TaskBoard(persist_path=os.path.join(tmpdir, "tasks.json"))
            board.add("Auto task")

            tm = TeammateManager(
                llm_client=fake_llm,
                registry=_make_registry(tmpdir),
                workspace_root=tmpdir,
                team_dir=tmpdir,
                task_board=board,
            )
            tm.spawn("worker", role="assistant")

            # First: send inbox message
            tm.send("worker", "Initial task")

            # Collect all notifications — both inbox response and auto-claim
            # can arrive in the same drain window since WORK falls through
            # to IDLE in the same loop iteration.
            deadline = time.time() + 30
            all_notifs = []
            while time.time() < deadline:
                batch = tm.drain()
                if batch:
                    all_notifs.extend(batch)
                    # Check if we got both: at least one normal response
                    # and at least one auto-claim
                    has_auto = any("auto-claimed" in n["message"] for n in all_notifs)
                    if has_auto:
                        break
                time.sleep(0.5)

            tm.shutdown("worker")
            assert len(all_notifs) >= 2
            assert any("auto-claimed" in n["message"] for n in all_notifs)
            task = board.get(1)
            assert task["owner"] == "worker"


# ── Autonomous claiming ──


class TestAutonomousClaiming:
    def test_role_matched_auto_claim(self):
        """Teammate with matching role auto-claims a role-restricted task."""
        with tempfile.TemporaryDirectory() as tmpdir:
            fake_llm = FakeLLM(response_text="Refactoring complete.")
            board = TaskBoard(persist_path=os.path.join(tmpdir, "tasks.json"))
            board.add("Refactor auth module", required_role="coder")

            tm = TeammateManager(
                llm_client=fake_llm,
                registry=_make_registry(tmpdir),
                workspace_root=tmpdir,
                team_dir=tmpdir,
                task_board=board,
            )
            tm.spawn("coder_bot", role="coder")

            deadline = time.time() + 30
            notifications = []
            while time.time() < deadline:
                notifications = tm.drain()
                if notifications:
                    break
                time.sleep(0.5)

            tm.shutdown("coder_bot")
            assert len(notifications) >= 1
            task = board.get(1)
            assert task["owner"] == "coder_bot"
            assert task["status"] == "in_progress"
            assert task["claim_source"] == "auto"

    def test_role_mismatch_no_claim(self):
        """Teammate with wrong role does not claim a role-restricted task."""
        with tempfile.TemporaryDirectory() as tmpdir:
            fake_llm = FakeLLM(response_text="Nothing to do.")
            board = TaskBoard(persist_path=os.path.join(tmpdir, "tasks.json"))
            board.add("Coding task", required_role="coder")

            tm = TeammateManager(
                llm_client=fake_llm,
                registry=_make_registry(tmpdir),
                workspace_root=tmpdir,
                team_dir=tmpdir,
                task_board=board,
            )
            tm.spawn("research_bot", role="researcher")

            time.sleep(8)
            task = board.get(1)
            assert task["owner"] == ""
            assert task["status"] == "pending"
            tm.shutdown("research_bot")
