"""Tests for TeammateManager -- roster, inbox, envelope, spawn, send, shutdown, list, drain, protocol, tools."""

import os
import tempfile
import threading
import time

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


# -- Helpers --


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


# -- Roster --


class TestRoster:
    def test_load_empty(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            roster = _load_roster(tmpdir)
            assert roster["team_name"] == "default"
            assert roster["members"] == []

    def test_save_and_load(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            roster = {"team_name": "alpha", "members": [{"name": "bob", "role": "coder"}]}
            _save_roster(tmpdir, roster)
            loaded = _load_roster(tmpdir)
            assert loaded["team_name"] == "alpha"
            assert len(loaded["members"]) == 1

    def test_find_member(self):
        roster = {"team_name": "test", "members": [
            {"name": "alice", "role": "researcher"},
            {"name": "bob", "role": "coder"},
        ]}
        assert _roster_member(roster, "alice")["role"] == "researcher"
        assert _roster_member(roster, "bob")["role"] == "coder"
        assert _roster_member(roster, "charlie") is None


# -- Inbox --


class TestInbox:
    def test_send_and_read(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            env = _make_envelope("lead", "Hello from lead")
            _send_to_inbox(tmpdir, "alice", env)
            msgs = _read_inbox(tmpdir, "alice")
            assert len(msgs) == 1
            assert msgs[0]["content"] == "Hello from lead"
            assert msgs[0]["from"] == "lead"

    def test_read_clears_inbox(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            _send_to_inbox(tmpdir, "alice", _make_envelope("lead", "msg1"))
            _read_inbox(tmpdir, "alice")
            msgs = _read_inbox(tmpdir, "alice")
            assert msgs == []

    def test_read_empty_inbox(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            msgs = _read_inbox(tmpdir, "nobody")
            assert msgs == []

    def test_multiple_messages(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            for i in range(3):
                _send_to_inbox(tmpdir, "alice", _make_envelope("lead", f"msg {i}"))
            msgs = _read_inbox(tmpdir, "alice")
            assert len(msgs) == 3


# -- Envelope --


class TestEnvelope:
    def test_has_fields(self):
        env = _make_envelope("lead", "Do something")
        assert env["type"] == "message"
        assert env["from"] == "lead"
        assert env["content"] == "Do something"
        assert "timestamp" in env

    def test_custom_type(self):
        env = _make_envelope("lead", "shutdown", msg_type="control")
        assert env["type"] == "control"


# -- TeammateManager CRUD --


class TestTeammateManagerSpawn:
    def test_spawn_creates_member(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tm = TeammateManager(
                llm_client=FakeLLM(),
                registry=_make_registry(),
                workspace_root=tmpdir,
                team_dir=tmpdir,
            )
            member = tm.spawn("researcher", role="research specialist")
            assert member["name"] == "researcher"
            assert member["role"] == "research specialist"
            assert member["status"] == "active"
            tm.shutdown("researcher")

    def test_spawn_updates_roster(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tm = TeammateManager(
                llm_client=FakeLLM(),
                registry=_make_registry(),
                workspace_root=tmpdir,
                team_dir=tmpdir,
            )
            tm.spawn("alice")
            members = tm.list()
            assert len(members) == 1
            assert members[0]["name"] == "alice"
            tm.shutdown("alice")

    def test_spawn_duplicate_raises(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tm = TeammateManager(
                llm_client=FakeLLM(),
                registry=_make_registry(),
                workspace_root=tmpdir,
                team_dir=tmpdir,
            )
            tm.spawn("alice")
            with pytest.raises(ValueError, match="already"):
                tm.spawn("alice")
            tm.shutdown("alice")


class TestTeammateManagerSend:
    def test_send_to_teammate(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tm = TeammateManager(
                llm_client=FakeLLM(),
                registry=_make_registry(),
                workspace_root=tmpdir,
                team_dir=tmpdir,
            )
            tm.spawn("bob")
            result = tm.send("bob", "Check the auth module")
            assert result["status"] == "sent"
            tm.shutdown("bob")

    def test_send_to_nonexistent_raises(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tm = TeammateManager(
                llm_client=FakeLLM(),
                registry=_make_registry(),
                workspace_root=tmpdir,
                team_dir=tmpdir,
            )
            with pytest.raises(KeyError):
                tm.send("ghost", "Hello?")


class TestTeammateManagerShutdown:
    def test_shutdown_removes_from_active(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tm = TeammateManager(
                llm_client=FakeLLM(),
                registry=_make_registry(),
                workspace_root=tmpdir,
                team_dir=tmpdir,
            )
            tm.spawn("alice")
            result = tm.shutdown("alice")
            assert result["status"] == "shutdown"

    def test_shutdown_nonexistent_raises(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tm = TeammateManager(
                llm_client=FakeLLM(),
                registry=_make_registry(),
                workspace_root=tmpdir,
                team_dir=tmpdir,
            )
            with pytest.raises(KeyError):
                tm.shutdown("ghost")

    def test_shutdown_updates_roster_status(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tm = TeammateManager(
                llm_client=FakeLLM(),
                registry=_make_registry(),
                workspace_root=tmpdir,
                team_dir=tmpdir,
            )
            tm.spawn("alice")
            tm.shutdown("alice")
            members = tm.list()
            assert members[0]["status"] == "shutdown"


class TestTeammateManagerList:
    def test_list_empty(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tm = TeammateManager(
                llm_client=FakeLLM(),
                registry=_make_registry(),
                workspace_root=tmpdir,
                team_dir=tmpdir,
            )
            assert tm.list() == []

    def test_list_multiple(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tm = TeammateManager(
                llm_client=FakeLLM(),
                registry=_make_registry(),
                workspace_root=tmpdir,
                team_dir=tmpdir,
            )
            tm.spawn("alice", role="researcher")
            tm.spawn("bob", role="coder")
            members = tm.list()
            assert len(members) == 2
            tm.shutdown("alice")
            tm.shutdown("bob")


# -- Drain --


class TestDrain:
    def test_empty_drain(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tm = TeammateManager(
                llm_client=FakeLLM(),
                registry=_make_registry(),
                workspace_root=tmpdir,
                team_dir=tmpdir,
            )
            assert tm.drain() == []

    def test_drain_consumes_queue(self):
        """Manually inject a notification and verify drain picks it up."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tm = TeammateManager(
                llm_client=FakeLLM(),
                registry=_make_registry(),
                workspace_root=tmpdir,
                team_dir=tmpdir,
            )
            tm._notifications.put({"from": "test", "message": "hello"})
            result = tm.drain()
            assert len(result) == 1
            assert result[0]["from"] == "test"
            # Second drain empty
            assert tm.drain() == []


# -- RequestTracker --


class TestRequestTracker:
    def test_create_returns_pending_record(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tracker = RequestTracker(tmpdir)
            record = tracker.create("shutdown", "lead", "alice")
            assert record["request_id"] == "req_001"
            assert record["kind"] == "shutdown"
            assert record["from"] == "lead"
            assert record["to"] == "alice"
            assert record["status"] == "pending"

    def test_create_auto_increments_id(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tracker = RequestTracker(tmpdir)
            r1 = tracker.create("shutdown", "lead", "alice")
            r2 = tracker.create("plan_approval", "alice", "lead")
            assert r1["request_id"] == "req_001"
            assert r2["request_id"] == "req_002"

    def test_get_existing(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tracker = RequestTracker(tmpdir)
            tracker.create("shutdown", "lead", "alice")
            record = tracker.get("req_001")
            assert record is not None
            assert record["kind"] == "shutdown"

    def test_get_nonexistent(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tracker = RequestTracker(tmpdir)
            assert tracker.get("req_999") is None

    def test_update_status(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tracker = RequestTracker(tmpdir)
            tracker.create("shutdown", "lead", "alice")
            updated = tracker.update_status("req_001", "approved")
            assert updated["status"] == "approved"
            # Persisted
            assert tracker.get("req_001")["status"] == "approved"

    def test_update_with_feedback(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tracker = RequestTracker(tmpdir)
            tracker.create("plan_approval", "alice", "lead")
            updated = tracker.update_status("req_001", "rejected", feedback="Needs more detail")
            assert updated["feedback"] == "Needs more detail"

    def test_update_nonexistent_raises(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tracker = RequestTracker(tmpdir)
            with pytest.raises(KeyError):
                tracker.update_status("req_999", "approved")

    def test_list_all(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tracker = RequestTracker(tmpdir)
            tracker.create("shutdown", "lead", "alice")
            tracker.create("plan_approval", "bob", "lead")
            assert len(tracker.list()) == 2

    def test_list_filter_by_status(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tracker = RequestTracker(tmpdir)
            tracker.create("shutdown", "lead", "alice")
            tracker.create("plan_approval", "bob", "lead")
            tracker.update_status("req_001", "approved")
            pending = tracker.list(status="pending")
            assert len(pending) == 1
            assert pending[0]["kind"] == "plan_approval"

    def test_persistence(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tracker1 = RequestTracker(tmpdir)
            tracker1.create("shutdown", "lead", "alice")
            # New tracker instance reads from same dir
            tracker2 = RequestTracker(tmpdir)
            assert tracker2.get("req_001") is not None
            # Next ID should be 2
            r2 = tracker2.create("shutdown", "lead", "bob")
            assert r2["request_id"] == "req_002"


# -- Protocol envelope --


class TestProtocolEnvelope:
    def test_has_required_fields(self):
        env = _make_protocol_envelope(
            "shutdown_request", "lead", "alice", "req_001",
        )
        assert env["type"] == "shutdown_request"
        assert env["from"] == "lead"
        assert env["to"] == "alice"
        assert env["request_id"] == "req_001"
        assert "timestamp" in env

    def test_default_payload(self):
        env = _make_protocol_envelope("test", "a", "b", "req_001")
        assert env["payload"] == {}

    def test_custom_payload(self):
        env = _make_protocol_envelope(
            "test", "a", "b", "req_001", payload={"key": "val"},
        )
        assert env["payload"]["key"] == "val"


# -- Graceful shutdown protocol --


class TestRequestShutdown:
    def test_creates_tracked_request(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tm = TeammateManager(
                llm_client=FakeLLM(),
                registry=_make_registry(),
                workspace_root=tmpdir,
                team_dir=tmpdir,
            )
            tm.spawn("alice")
            record = tm.request_shutdown("alice")
            assert record["kind"] == "shutdown"
            assert record["to"] == "alice"
            assert record["status"] == "pending"
            tm.shutdown("alice")

    def test_sends_envelope_to_inbox(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tm = TeammateManager(
                llm_client=FakeLLM(),
                registry=_make_registry(),
                workspace_root=tmpdir,
                team_dir=tmpdir,
            )
            tm.spawn("alice")
            tm.request_shutdown("alice")
            # Read inbox directly
            msgs = _read_inbox(tmpdir, "alice")
            assert len(msgs) == 1
            assert msgs[0]["type"] == "shutdown_request"
            assert msgs[0]["request_id"] == "req_001"
            tm.shutdown("alice")

    def test_nonexistent_raises(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tm = TeammateManager(
                llm_client=FakeLLM(),
                registry=_make_registry(),
                workspace_root=tmpdir,
                team_dir=tmpdir,
            )
            with pytest.raises(KeyError):
                tm.request_shutdown("ghost")

    def test_graceful_shutdown_flow(self):
        """Full flow: request_shutdown -> teammate auto-approves -> stops."""
        with tempfile.TemporaryDirectory() as tmpdir:
            fake_llm = FakeLLM(response_text="Shutting down.")
            tm = TeammateManager(
                llm_client=fake_llm,
                registry=_make_registry(),
                workspace_root=tmpdir,
                team_dir=tmpdir,
            )
            tm.spawn("worker")

            # Send graceful shutdown request
            record = tm.request_shutdown("worker")
            assert record["request_id"] == "req_001"

            # Wait for teammate to process the protocol message
            deadline = time.time() + 15
            notifications = []
            while time.time() < deadline:
                notifications = tm.drain()
                if notifications:
                    break
                time.sleep(0.5)

            # Should have received shutdown acceptance
            assert len(notifications) >= 1
            assert "accepted shutdown" in notifications[0]["message"]

            # Request should be approved
            req = tm.list_requests(status="approved")
            assert len(req) >= 1
            assert req[0]["request_id"] == "req_001"


# -- Plan approval protocol --


class TestSubmitPlan:
    def test_creates_plan_request(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tm = TeammateManager(
                llm_client=FakeLLM(),
                registry=_make_registry(),
                workspace_root=tmpdir,
                team_dir=tmpdir,
            )
            record = tm.submit_plan("alice", "Refactor auth module")
            assert record["kind"] == "plan_approval"
            assert record["from"] == "alice"
            assert record["payload"]["plan"] == "Refactor auth module"

    def test_queues_notification(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tm = TeammateManager(
                llm_client=FakeLLM(),
                registry=_make_registry(),
                workspace_root=tmpdir,
                team_dir=tmpdir,
            )
            tm.submit_plan("bob", "Write tests")
            notifs = tm.drain()
            assert len(notifs) == 1
            assert "Plan Approval Request" in notifs[0]["message"]
            assert "Write tests" in notifs[0]["message"]


class TestReviewRequest:
    def test_approve_request(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tm = TeammateManager(
                llm_client=FakeLLM(),
                registry=_make_registry(),
                workspace_root=tmpdir,
                team_dir=tmpdir,
            )
            tm.submit_plan("alice", "My plan")
            result = tm.review_request("req_001", approve=True, feedback="Looks good")
            assert result["status"] == "approved"
            # Verify persisted
            req = tm.list_requests(status="approved")
            assert len(req) == 1

    def test_reject_request(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tm = TeammateManager(
                llm_client=FakeLLM(),
                registry=_make_registry(),
                workspace_root=tmpdir,
                team_dir=tmpdir,
            )
            tm.submit_plan("alice", "Bad plan")
            result = tm.review_request("req_001", approve=False, feedback="Not enough detail")
            assert result["status"] == "rejected"
            req = tm.list_requests(status="rejected")
            assert len(req) == 1

    def test_nonexistent_raises(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tm = TeammateManager(
                llm_client=FakeLLM(),
                registry=_make_registry(),
                workspace_root=tmpdir,
                team_dir=tmpdir,
            )
            with pytest.raises(KeyError):
                tm.review_request("req_999")

    def test_not_pending_raises(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tm = TeammateManager(
                llm_client=FakeLLM(),
                registry=_make_registry(),
                workspace_root=tmpdir,
                team_dir=tmpdir,
            )
            tm.submit_plan("alice", "Plan")
            tm.review_request("req_001", approve=True)
            # Already approved -- should raise
            with pytest.raises(ValueError, match="not pending"):
                tm.review_request("req_001", approve=False)

    def test_sends_response_to_teammate(self):
        """Review sends plan_approval_response envelope to teammate inbox."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tm = TeammateManager(
                llm_client=FakeLLM(),
                registry=_make_registry(),
                workspace_root=tmpdir,
                team_dir=tmpdir,
            )
            tm.spawn("alice")
            tm.submit_plan("alice", "My plan")
            tm.review_request("req_001", approve=True, feedback="Go ahead")
            # Check alice's inbox
            msgs = _read_inbox(tmpdir, "alice")
            assert len(msgs) == 1
            assert msgs[0]["type"] == "plan_approval_response"
            assert msgs[0]["payload"]["approved"] is True
            tm.shutdown("alice")


class TestListRequests:
    def test_list_empty(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tm = TeammateManager(
                llm_client=FakeLLM(),
                registry=_make_registry(),
                workspace_root=tmpdir,
                team_dir=tmpdir,
            )
            assert tm.list_requests() == []

    def test_list_filter_status(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tm = TeammateManager(
                llm_client=FakeLLM(),
                registry=_make_registry(),
                workspace_root=tmpdir,
                team_dir=tmpdir,
            )
            tm.submit_plan("alice", "Plan A")
            tm.submit_plan("bob", "Plan B")
            tm.review_request("req_001", approve=True)
            pending = tm.list_requests(status="pending")
            assert len(pending) == 1
            assert pending[0]["from"] == "bob"


# -- Handle protocol --


class TestHandleProtocol:
    def test_shutdown_request_auto_approves(self):
        """Simulate receiving a shutdown_request protocol message."""
        with tempfile.TemporaryDirectory() as tmpdir:
            fake_llm = FakeLLM()
            tm = TeammateManager(
                llm_client=fake_llm,
                registry=_make_registry(),
                workspace_root=tmpdir,
                team_dir=tmpdir,
            )
            # Create a shutdown request record manually
            tracker = tm._tracker
            record = tracker.create("shutdown", "lead", "worker")

            # Simulate daemon state
            stop_event = threading.Event()
            state = {"stop_event": stop_event, "messages": []}

            # Call _handle_protocol directly
            envelope = _make_protocol_envelope(
                "shutdown_request", "lead", "worker", record["request_id"],
            )
            tm._handle_protocol("worker", envelope, state)

            # Should be approved
            assert tracker.get(record["request_id"])["status"] == "approved"
            # Stop event should be set
            assert stop_event.is_set()

    def test_plan_approval_response_feeds_conversation(self):
        """Teammate receives plan review result into its conversation."""
        with tempfile.TemporaryDirectory() as tmpdir:
            fake_llm = FakeLLM()
            tm = TeammateManager(
                llm_client=fake_llm,
                registry=_make_registry(),
                workspace_root=tmpdir,
                team_dir=tmpdir,
            )
            tracker = tm._tracker
            record = tracker.create("plan_approval", "worker", "lead")

            state = {"stop_event": threading.Event(), "messages": []}
            envelope = _make_protocol_envelope(
                "plan_approval_response", "lead", "worker", record["request_id"],
                payload={"approved": True, "feedback": "Looks good"},
            )
            tm._handle_protocol("worker", envelope, state)

            # Status should be updated
            assert tracker.get(record["request_id"])["status"] == "approved"
            # Conversation should have review result
            assert len(state["messages"]) == 1
            assert "approved" in state["messages"][0]["content"]

    def test_plan_rejection_feeds_conversation(self):
        """Rejected plan gets feedback in conversation."""
        with tempfile.TemporaryDirectory() as tmpdir:
            fake_llm = FakeLLM()
            tm = TeammateManager(
                llm_client=fake_llm,
                registry=_make_registry(),
                workspace_root=tmpdir,
                team_dir=tmpdir,
            )
            tracker = tm._tracker
            record = tracker.create("plan_approval", "worker", "lead")

            state = {"stop_event": threading.Event(), "messages": []}
            envelope = _make_protocol_envelope(
                "plan_approval_response", "lead", "worker", record["request_id"],
                payload={"approved": False, "feedback": "Need more tests"},
            )
            tm._handle_protocol("worker", envelope, state)

            assert tracker.get(record["request_id"])["status"] == "rejected"
            assert "rejected" in state["messages"][0]["content"]
            assert "Need more tests" in state["messages"][0]["content"]


# -- Protocol tool registration --


class TestProtocolTools:
    def test_team_request_shutdown_tool(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tm = TeammateManager(
                llm_client=FakeLLM(),
                registry=_make_registry(tmpdir),
                workspace_root=tmpdir,
                team_dir=tmpdir,
            )
            registry = _make_registry(tmpdir)
            register_team_tools(registry, tm)

            registry.call("team_spawn", {"name": "worker"})
            output = registry.call("team_request_shutdown", {"name": "worker"})
            assert "req_001" in output
            assert "shutdown request" in output
            tm.shutdown("worker")

    def test_team_submit_plan_tool(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tm = TeammateManager(
                llm_client=FakeLLM(),
                registry=_make_registry(tmpdir),
                workspace_root=tmpdir,
                team_dir=tmpdir,
            )
            registry = _make_registry(tmpdir)
            register_team_tools(registry, tm)

            output = registry.call("team_submit_plan", {
                "name": "analyst",
                "plan": "Investigate the bug",
            })
            assert "req_001" in output
            assert "Plan submitted" in output

    def test_team_review_tool(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tm = TeammateManager(
                llm_client=FakeLLM(),
                registry=_make_registry(tmpdir),
                workspace_root=tmpdir,
                team_dir=tmpdir,
            )
            registry = _make_registry(tmpdir)
            register_team_tools(registry, tm)

            registry.call("team_submit_plan", {
                "name": "analyst",
                "plan": "Do the thing",
            })
            output = registry.call("team_review", {
                "request_id": "req_001",
                "approve": True,
                "feedback": "Go ahead",
            })
            assert "approved" in output

    def test_team_requests_tool(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tm = TeammateManager(
                llm_client=FakeLLM(),
                registry=_make_registry(tmpdir),
                workspace_root=tmpdir,
                team_dir=tmpdir,
            )
            registry = _make_registry(tmpdir)
            register_team_tools(registry, tm)

            registry.call("team_submit_plan", {"name": "alice", "plan": "Plan A"})
            registry.call("team_submit_plan", {"name": "bob", "plan": "Plan B"})
            output = registry.call("team_requests", {})
            assert "req_001" in output
            assert "req_002" in output

    def test_team_requests_filter_status(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tm = TeammateManager(
                llm_client=FakeLLM(),
                registry=_make_registry(tmpdir),
                workspace_root=tmpdir,
                team_dir=tmpdir,
            )
            registry = _make_registry(tmpdir)
            register_team_tools(registry, tm)

            registry.call("team_submit_plan", {"name": "alice", "plan": "Plan A"})
            registry.call("team_review", {"request_id": "req_001", "approve": True})
            output = registry.call("team_requests", {"status": "approved"})
            assert "req_001" in output

    def test_request_shutdown_empty_name_rejected(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tm = TeammateManager(
                llm_client=FakeLLM(),
                registry=_make_registry(tmpdir),
                workspace_root=tmpdir,
                team_dir=tmpdir,
            )
            registry = _make_registry(tmpdir)
            register_team_tools(registry, tm)

            with pytest.raises(RuntimeError, match="name is required"):
                registry.call("team_request_shutdown", {"name": ""})

    def test_submit_plan_missing_fields_rejected(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tm = TeammateManager(
                llm_client=FakeLLM(),
                registry=_make_registry(tmpdir),
                workspace_root=tmpdir,
                team_dir=tmpdir,
            )
            registry = _make_registry(tmpdir)
            register_team_tools(registry, tm)

            with pytest.raises(RuntimeError, match="plan is required"):
                registry.call("team_submit_plan", {"name": "alice", "plan": ""})

    def test_review_missing_request_id_rejected(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tm = TeammateManager(
                llm_client=FakeLLM(),
                registry=_make_registry(tmpdir),
                workspace_root=tmpdir,
                team_dir=tmpdir,
            )
            registry = _make_registry(tmpdir)
            register_team_tools(registry, tm)

            with pytest.raises(RuntimeError, match="request_id is required"):
                registry.call("team_review", {"request_id": ""})


# -- Tool registration --


class TestTeamTools:
    def test_team_spawn_tool(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tm = TeammateManager(
                llm_client=FakeLLM(),
                registry=_make_registry(tmpdir),
                workspace_root=tmpdir,
                team_dir=tmpdir,
            )
            registry = _make_registry(tmpdir)
            register_team_tools(registry, tm)

            output = registry.call("team_spawn", {"name": "helper", "role": "assistant"})
            assert "Spawned teammate 'helper'" in output
            tm.shutdown("helper")

    def test_team_send_tool(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tm = TeammateManager(
                llm_client=FakeLLM(),
                registry=_make_registry(tmpdir),
                workspace_root=tmpdir,
                team_dir=tmpdir,
            )
            registry = _make_registry(tmpdir)
            register_team_tools(registry, tm)

            registry.call("team_spawn", {"name": "bob"})
            output = registry.call("team_send", {"name": "bob", "content": "Do thing"})
            assert "Sent to 'bob'" in output
            tm.shutdown("bob")

    def test_team_list_tool(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tm = TeammateManager(
                llm_client=FakeLLM(),
                registry=_make_registry(tmpdir),
                workspace_root=tmpdir,
                team_dir=tmpdir,
            )
            registry = _make_registry(tmpdir)
            register_team_tools(registry, tm)

            registry.call("team_spawn", {"name": "alice", "role": "researcher"})
            output = registry.call("team_list", {})
            assert "alice" in output
            tm.shutdown("alice")

    def test_team_shutdown_tool(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tm = TeammateManager(
                llm_client=FakeLLM(),
                registry=_make_registry(tmpdir),
                workspace_root=tmpdir,
                team_dir=tmpdir,
            )
            registry = _make_registry(tmpdir)
            register_team_tools(registry, tm)

            registry.call("team_spawn", {"name": "temp"})
            output = registry.call("team_shutdown", {"name": "temp"})
            assert "Shutdown teammate 'temp'" in output

    def test_spawn_empty_name_rejected(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tm = TeammateManager(
                llm_client=FakeLLM(),
                registry=_make_registry(tmpdir),
                workspace_root=tmpdir,
                team_dir=tmpdir,
            )
            registry = _make_registry(tmpdir)
            register_team_tools(registry, tm)

            with pytest.raises(RuntimeError, match="name is required"):
                registry.call("team_spawn", {"name": ""})

    def test_send_missing_content_rejected(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tm = TeammateManager(
                llm_client=FakeLLM(),
                registry=_make_registry(tmpdir),
                workspace_root=tmpdir,
                team_dir=tmpdir,
            )
            registry = _make_registry(tmpdir)
            register_team_tools(registry, tm)

            with pytest.raises(RuntimeError, match="content is required"):
                registry.call("team_send", {"name": "nobody", "content": ""})


# -- Autonomous claiming --


class TestTaskBoardClaim:
    """Tests for claim_task, is_claimable, scan_unclaimed."""

    def test_is_claimable_pending_unowned(self):
        board = TaskBoard()
        board.add("Task")
        task = board.get(1)
        assert is_claimable(task) is True

    def test_not_claimable_if_owned(self):
        board = TaskBoard()
        board.add("Task", owner="alice")
        task = board.get(1)
        assert is_claimable(task) is False

    def test_not_claimable_if_blocked(self):
        board = TaskBoard()
        board.add("Blocker")
        board.add("Blocked", blocked_by=[1])
        task = board.get(2)
        assert is_claimable(task) is False

    def test_not_claimable_if_in_progress(self):
        board = TaskBoard()
        board.add("Task")
        board.start(1)
        task = board.get(1)
        assert is_claimable(task) is False

    def test_role_match_allows_claim(self):
        board = TaskBoard()
        board.add("Task", required_role="coder")
        task = board.get(1)
        assert is_claimable(task, role="junior coder") is True
        assert is_claimable(task, role="researcher") is False

    def test_no_role_can_claim_open_task(self):
        board = TaskBoard()
        board.add("Task")
        task = board.get(1)
        assert is_claimable(task, role="anything") is True
        assert is_claimable(task) is True

    def test_no_role_cannot_claim_role_restricted(self):
        board = TaskBoard()
        board.add("Task", required_role="coder")
        task = board.get(1)
        assert is_claimable(task, role=None) is False

    def test_claim_task_success(self):
        board = TaskBoard()
        board.add("Task")
        task = board.claim_task(1, owner="alice", role="coder", source="auto")
        assert task["owner"] == "alice"
        assert task["status"] == "in_progress"
        assert task["claim_role"] == "coder"
        assert task["claim_source"] == "auto"
        assert task["claimed_at"] is not None

    def test_claim_task_not_claimable_raises(self):
        board = TaskBoard()
        board.add("Task", owner="bob")
        with pytest.raises(ValueError, match="not claimable"):
            board.claim_task(1, owner="alice")

    def test_claim_task_role_mismatch_raises(self):
        board = TaskBoard()
        board.add("Task", required_role="coder")
        with pytest.raises(ValueError, match="not claimable"):
            board.claim_task(1, owner="alice", role="researcher")

    def test_scan_unclaimed(self):
        board = TaskBoard()
        board.add("Task A")
        board.add("Task B", owner="bob")
        board.add("Task C", required_role="coder")
        unclaimed = board.scan_unclaimed()
        assert len(unclaimed) == 1
        assert unclaimed[0]["subject"] == "Task A"

    def test_scan_unclaimed_with_role(self):
        board = TaskBoard()
        board.add("Task A")
        board.add("Task B", required_role="coder")
        unclaimed = board.scan_unclaimed(role="coder")
        assert len(unclaimed) == 2

    def test_scan_unclaimed_ordered_by_id(self):
        board = TaskBoard()
        board.add("Third")
        board.add("First")
        board.add("Second")
        unclaimed = board.scan_unclaimed()
        assert [t["id"] for t in unclaimed] == [1, 2, 3]

    def test_claim_atomicity(self):
        """Two threads racing to claim the same task."""
        import json
        board = TaskBoard()
        board.add("Contested task")
        results = []

        def claim(name):
            try:
                board.claim_task(1, owner=name)
                results.append((name, True))
            except ValueError:
                results.append((name, False))

        t1 = threading.Thread(target=claim, args=("alice",))
        t2 = threading.Thread(target=claim, args=("bob",))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        successes = [r for r in results if r[1]]
        assert len(successes) == 1
        task = board.get(1)
        assert task["owner"] in ("alice", "bob")

    def test_claim_event_log(self):
        import json
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "tasks.json")
            board = TaskBoard(persist_path=path)
            board.add("Task")
            board.claim_task(1, owner="alice", role="coder", source="auto")

            events_path = os.path.join(tmpdir, "claim_events.jsonl")
            assert os.path.exists(events_path)
            with open(events_path) as f:
                event = json.loads(f.readline())
            assert event["event"] == "claimed"
            assert event["task_id"] == 1
            assert event["owner"] == "alice"
            assert event["source"] == "auto"


# -- Identity re-injection --


class TestIdentityReInjection:
    def test_ensure_identity_adds_system_message(self):
        """When identity is missing, _ensure_identity prepends it."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tm = TeammateManager(
                llm_client=FakeLLM(),
                registry=_make_registry(tmpdir),
                workspace_root=tmpdir,
                team_dir=tmpdir,
            )
            messages = [{"role": "user", "content": "Hello"}]
            tm._ensure_identity(messages, "analyst", "research specialist")
            assert len(messages) == 2
            assert messages[0]["role"] == "system"
            assert "analyst" in messages[0]["content"]

    def test_ensure_identity_skips_if_present(self):
        """When identity already exists, no duplicate is added."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tm = TeammateManager(
                llm_client=FakeLLM(),
                registry=_make_registry(tmpdir),
                workspace_root=tmpdir,
                team_dir=tmpdir,
            )
            messages = [_make_system_message("analyst", "researcher")]
            original_len = len(messages)
            tm._ensure_identity(messages, "analyst", "researcher")
            assert len(messages) == original_len
