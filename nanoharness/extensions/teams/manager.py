"""Team system — long-lived teammates with roster, inbox, and independent loops.

Unlike subagents (one-shot delegation), teammates are long-lived.
They have their own Think→Act→Observe loop running in a daemon thread,
communicating via JSONL inboxes.

Three parts:
    1. Roster         — who's on the team (persisted to .team/config.json)
    2. Inbox          — per-teammate message queue (.team/inbox/{name}.jsonl)
    3. TeammateManager — spawn/send/shutdown, drain() for notifications

Key insight: teammates drain their inbox → run their own loop → put
responses into a notification queue. The main loop picks them up via
get_full_context() → drain().

Usage:
    tm = TeammateManager(llm_client=llm, registry=registry, workspace_root=root)
    tm.spawn("researcher", role="research specialist")
    tm.send("researcher", "Look into the auth module")
    notifications = tm.drain()  # responses from teammates
"""

import json
import os
import queue
import re
import threading
import time
from typing import Any, Dict, List, Optional

from nanoharness.core.base import BaseToolRegistry


# ── Defaults ──

_TEAM_DIR = ".team"
_CONFIG_FILE = "config.json"
_INBOX_DIR = "inbox"
_MATE_MAX_TURNS = 8
_MATE_CHECK_INTERVAL = 5  # seconds between inbox checks
_MATE_IDLE_MAX_CHECKS = 12  # ~60s of idle checks before giving up
_MATE_IDLE_CHECK_INTERVAL = 5  # seconds between idle phase checks
_REQUESTS_DIR = "requests"
_TEAMMATE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
_REQUEST_ID_RE = re.compile(r"^req_[0-9]+$")


# ── Roster ──


def _load_roster(team_dir: str) -> Dict[str, Any]:
    """Load team roster from config.json. Returns empty structure if missing."""
    config_path = os.path.join(team_dir, _CONFIG_FILE)
    if not os.path.exists(config_path):
        return {"team_name": "default", "members": []}
    with open(config_path, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_roster(team_dir: str, roster: Dict[str, Any]):
    """Save team roster to config.json."""
    config_path = os.path.join(team_dir, _CONFIG_FILE)
    tmp = config_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(roster, f, indent=2, ensure_ascii=False)
    os.replace(tmp, config_path)


def _roster_member(roster: Dict[str, Any], name: str) -> Optional[Dict]:
    """Find a member by name in the roster."""
    for m in roster["members"]:
        if m["name"] == name:
            return m
    return None


# ── Inbox ──


def _inbox_path(team_dir: str, name: str) -> str:
    if not _TEAMMATE_NAME_RE.fullmatch(name):
        raise ValueError("Unsafe teammate name")
    return os.path.join(team_dir, _INBOX_DIR, f"{name}.jsonl")


def _send_to_inbox(team_dir: str, name: str, envelope: Dict):
    """Append a message envelope to a teammate's JSONL inbox."""
    path = _inbox_path(team_dir, name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(envelope, ensure_ascii=False) + "\n")


def _read_inbox(team_dir: str, name: str) -> List[Dict]:
    """Read and clear a teammate's inbox. Returns all pending messages."""
    path = _inbox_path(team_dir, name)
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        lines = f.readlines()
    # Clear inbox
    open(path, "w").close()
    messages = []
    for line in lines:
        line = line.strip()
        if line:
            messages.append(json.loads(line))
    return messages


def _inbox_has_messages(team_dir: str, name: str) -> bool:
    """Check if a teammate's inbox has messages without consuming them."""
    path = _inbox_path(team_dir, name)
    if not os.path.exists(path):
        return False
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                return True
    return False


# ── Message envelope ──


def _make_envelope(from_name: str, content: str, msg_type: str = "message") -> Dict:
    return {
        "type": msg_type,
        "from": from_name,
        "content": content,
        "timestamp": time.time(),
    }


def _make_protocol_envelope(
    msg_type: str, from_name: str, to_name: str, request_id: str, payload: dict = None,
) -> Dict:
    """Create a structured protocol envelope with request tracking."""
    return {
        "type": msg_type,
        "from": from_name,
        "to": to_name,
        "request_id": request_id,
        "payload": payload or {},
        "timestamp": time.time(),
    }


# ── Request tracker ──


class RequestTracker:
    """Track protocol requests with file persistence.

    Each request is stored as .team/requests/req_NNN.json.
    State machine: pending → approved | rejected | expired.
    """

    def __init__(self, team_dir: str):
        self._dir = os.path.join(team_dir, _REQUESTS_DIR)
        self._lock = threading.RLock()
        os.makedirs(self._dir, exist_ok=True)
        self._next_id = self._compute_next_id()

    def _compute_next_id(self) -> int:
        if not os.path.exists(self._dir):
            return 1
        existing = [f for f in os.listdir(self._dir) if f.endswith(".json")]
        if not existing:
            return 1
        ids = []
        for f in existing:
            try:
                ids.append(int(f.replace("req_", "").replace(".json", "")))
            except ValueError:
                pass
        return max(ids) + 1 if ids else 1

    def create(self, kind: str, from_name: str, to_name: str, payload: dict = None) -> Dict:
        with self._lock:
            req_id = f"req_{self._next_id:03d}"
            self._next_id += 1
            record = {
                "request_id": req_id,
                "kind": kind,
                "from": from_name,
                "to": to_name,
                "status": "pending",
                "payload": payload or {},
                "created_at": time.time(),
            }
            self._save(record)
            return record

    def get(self, request_id: str) -> Optional[Dict]:
        if not _REQUEST_ID_RE.fullmatch(request_id):
            raise ValueError("Invalid request ID")
        with self._lock:
            path = os.path.join(self._dir, f"{request_id}.json")
            if not os.path.exists(path):
                return None
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)

    def update_status(self, request_id: str, status: str, feedback: str = None) -> Dict:
        with self._lock:
            record = self.get(request_id)
            if record is None:
                raise KeyError(f"Request '{request_id}' not found")
            record["status"] = status
            if feedback:
                record["feedback"] = feedback
            record["updated_at"] = time.time()
            self._save(record)
            return record

    def list(self, status: str = None) -> List[Dict]:
        with self._lock:
            records = []
            if not os.path.exists(self._dir):
                return records
            for f in sorted(os.listdir(self._dir)):
                if not f.endswith(".json"):
                    continue
                with open(os.path.join(self._dir, f), "r", encoding="utf-8") as fh:
                    record = json.load(fh)
                    if status is None or record.get("status") == status:
                        records.append(record)
            return records

    def _save(self, record: Dict):
        path = os.path.join(self._dir, f"{record['request_id']}.json")
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(record, f, indent=2, ensure_ascii=False)
        os.replace(tmp, path)


# ── TeammateManager ──


class TeammateManager:
    """Manage long-lived teammates with inbox-based communication.

    Each teammate runs in a daemon thread with its own Think→Act→Observe loop.
    Communication happens via JSONL inboxes (send) and a shared notification
    queue (drain).
    """

    def __init__(
        self,
        llm_client,
        registry: BaseToolRegistry,
        workspace_root: str,
        team_dir: Optional[str] = None,
        task_board=None,
        max_turns: int = _MATE_MAX_TURNS,
        check_interval: float = _MATE_CHECK_INTERVAL,
        idle_max_checks: int = _MATE_IDLE_MAX_CHECKS,
        idle_check_interval: float = _MATE_IDLE_CHECK_INTERVAL,
        shutdown_timeout: float = 5.0,
        tool_whitelist: Optional[List[str]] = None,
    ):
        self._llm = llm_client
        self._registry = registry
        self._workspace_root = os.path.realpath(workspace_root)
        candidate = team_dir or os.path.join(self._workspace_root, _TEAM_DIR)
        self._team_dir = os.path.realpath(candidate)
        if os.path.commonpath([self._workspace_root, self._team_dir]) != self._workspace_root:
            raise ValueError("team_dir must stay within workspace_root")
        self._task_board = task_board
        self._max_turns = max_turns
        self._check_interval = check_interval
        self._idle_max_checks = idle_max_checks
        self._idle_check_interval = idle_check_interval
        self._shutdown_timeout = shutdown_timeout
        self._tool_whitelist = frozenset(tool_whitelist or [
            "file_read", "file_list", "file_find", "search_code", "list_files",
        ])
        self._notifications: queue.Queue = queue.Queue()
        self._lock = threading.Lock()
        self._inbox_lock = threading.RLock()
        self._tracker = RequestTracker(self._team_dir)
        self._closed = False

        # Active teammate state: {name: {"thread", "stop_event", "messages"}}
        self._mates: Dict[str, Dict[str, Any]] = {}

        os.makedirs(os.path.join(self._team_dir, _INBOX_DIR), exist_ok=True)

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def active_thread_names(self) -> List[str]:
        with self._lock:
            return sorted(
                name
                for name, mate in self._mates.items()
                if mate.get("thread") is not None and mate["thread"].is_alive()
            )

    def spawn(self, name: str, role: str = "assistant") -> Dict[str, Any]:
        """Spawn a new teammate.

        Creates a roster entry and starts the teammate's daemon loop.
        """
        if not _TEAMMATE_NAME_RE.fullmatch(name):
            raise ValueError(
                "Teammate name must be 1-64 safe characters: letters, numbers, _, ., -"
            )
        # Build state before reserving the name so roster and active state are atomic.
        stop_event = threading.Event()
        mate_state = {
            "thread": None,
            "stop_event": stop_event,
            "messages": [_make_system_message(name, role)],
            "role": role,
            "phase": "idle",
        }
        t = threading.Thread(
            target=self._mate_loop,
            args=(name, mate_state),
            daemon=True,
            name=f"mate-{name}",
        )
        mate_state["thread"] = t

        with self._lock:
            if self._closed:
                raise RuntimeError("TeammateManager is closed")
            if name in self._mates:
                raise ValueError(f"Teammate '{name}' already active")

            # Keep roster mutation and active-state reservation atomic.
            roster = _load_roster(self._team_dir)
            if _roster_member(roster, name):
                raise ValueError(f"Teammate '{name}' already in roster")

            member = {"name": name, "role": role, "status": "active", "created_at": time.time()}
            roster["members"].append(member)
            _save_roster(self._team_dir, roster)
            self._mates[name] = mate_state
        t.start()

        return member

    def send(self, name: str, content: str, from_name: str = "lead") -> Dict[str, Any]:
        """Send a message to a teammate's inbox."""
        with self._lock:
            if name not in self._mates:
                raise KeyError(f"Teammate '{name}' not found")

        envelope = _make_envelope(from_name, content)
        with self._inbox_lock:
            _send_to_inbox(self._team_dir, name, envelope)
        return {"to": name, "status": "sent", "content_preview": content[:100]}

    def shutdown(self, name: str) -> Dict[str, Any]:
        """Shutdown a teammate. Stops the daemon loop and updates roster."""
        with self._lock:
            mate = self._mates.pop(name, None)
            if mate is None:
                raise KeyError(f"Teammate '{name}' not found")
            mate["stop_event"].set()

        thread = mate.get("thread")
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=self._shutdown_timeout)
            if thread.is_alive():
                with self._lock:
                    self._mates[name] = mate
                raise RuntimeError(
                    f"Teammate '{name}' did not stop before timeout"
                )

        # Update roster
        with self._lock:
            roster = _load_roster(self._team_dir)
            member = _roster_member(roster, name)
            if member:
                member["status"] = "shutdown"
                _save_roster(self._team_dir, roster)

        return {"name": name, "status": "shutdown"}

    def list(self) -> List[Dict[str, Any]]:
        """List all team members from the roster."""
        with self._lock:
            roster = _load_roster(self._team_dir)
            return roster["members"]

    def drain(self) -> List[Dict[str, Any]]:
        """Pull all teammate responses (non-blocking)."""
        results = []
        while True:
            try:
                notif = self._notifications.get_nowait()
            except queue.Empty:
                break
            results.append(notif)
        return results

    def stop_all(self):
        """Shutdown all active teammates and wait for their loops to exit."""
        with self._lock:
            mates = list(self._mates.items())
            self._closed = True
            for name, mate in mates:
                mate["stop_event"].set()
        alive = []
        for name, mate in mates:
            thread = mate.get("thread")
            if thread is not None and thread is not threading.current_thread():
                thread.join(timeout=self._shutdown_timeout)
                if thread.is_alive():
                    alive.append(name)

        stopped = {name for name, _ in mates} - set(alive)
        with self._lock:
            for name in stopped:
                self._mates.pop(name, None)

        if stopped:
            roster = _load_roster(self._team_dir)
            for member in roster["members"]:
                if member.get("name") in stopped:
                    member["status"] = "shutdown"
            _save_roster(self._team_dir, roster)
        if alive:
            raise RuntimeError(
                "Teammate threads did not stop before timeout: " + ", ".join(alive)
            )

    close = stop_all

    # ── Protocol methods ──

    def request_shutdown(self, target: str, from_name: str = "lead") -> Dict:
        """Send graceful shutdown request to a teammate.

        Creates a tracked request and sends a shutdown_request protocol
        envelope to the teammate's inbox. The teammate auto-approves
        in its _handle_protocol handler.
        """
        with self._lock:
            if target not in self._mates:
                raise KeyError(f"Teammate '{target}' not found")

        record = self._tracker.create("shutdown", from_name, target)
        envelope = _make_protocol_envelope(
            "shutdown_request", from_name, target, record["request_id"],
        )
        with self._inbox_lock:
            _send_to_inbox(self._team_dir, target, envelope)
        return record

    def submit_plan(self, name: str, plan_text: str, to_name: str = "lead") -> Dict:
        """Submit a plan for lead's approval.

        Creates a plan_approval request and queues a notification for the lead.
        The lead reviews via team_review tool.
        """
        record = self._tracker.create(
            "plan_approval", name, to_name, payload={"plan": plan_text},
        )
        self._notifications.put({
            "from": name,
            "message": (
                f"[Plan Approval Request #{record['request_id']}]\n"
                f"From: {name}\nPlan: {plan_text}\n"
                f"Use team_review to approve or reject."
            ),
            "request_id": record["request_id"],
        })
        return record

    def review_request(self, request_id: str, approve: bool = True, feedback: str = "") -> Dict:
        """Review a pending request (approve or reject).

        Updates the request status and sends a response protocol envelope
        to the relevant teammate's inbox.
        """
        record = self._tracker.get(request_id)
        if record is None:
            raise KeyError(f"Request '{request_id}' not found")
        if record["status"] != "pending":
            raise ValueError(f"Request '{request_id}' is {record['status']}, not pending")

        status = "approved" if approve else "rejected"
        self._tracker.update_status(request_id, status, feedback=feedback)

        # Determine response type and target
        if record["kind"] == "shutdown":
            response_type = "shutdown_response"
            target = record["to"]
        else:
            response_type = "plan_approval_response"
            target = record["from"]

        envelope = _make_protocol_envelope(
            response_type, record["to"], target, request_id,
            payload={"approved": approve, "feedback": feedback},
        )

        with self._lock:
            if target in self._mates:
                with self._inbox_lock:
                    _send_to_inbox(self._team_dir, target, envelope)

        return {"request_id": request_id, "status": status}

    def list_requests(self, status: str = None) -> List[Dict]:
        """List tracked requests, optionally filtered by status."""
        return self._tracker.list(status=status)

    # ── Teammate loop ──

    def _mate_loop(self, name: str, state: Dict):
        """Daemon loop: WORK -> IDLE -> WORK -> ...

        WORK phase: drain inbox, handle protocol + regular messages.
        IDLE phase: check inbox (priority) then scan task board for
        claimable tasks. Returns to WORK if work is found.
        """
        stop_event = state["stop_event"]
        messages = state["messages"]
        role = state.get("role", "assistant")
        mate_tools = self._build_mate_tools()

        while not stop_event.is_set():
            # ── WORK phase ──
            with self._inbox_lock:
                inbox_msgs = _read_inbox(self._team_dir, name)
            if inbox_msgs:
                protocol_msgs = [env for env in inbox_msgs if env.get("request_id")]
                regular_msgs = [env for env in inbox_msgs if not env.get("request_id")]

                for env in protocol_msgs:
                    self._handle_protocol(name, env, state)
                    if stop_event.is_set():
                        return

                if regular_msgs:
                    for env in regular_msgs:
                        messages.append({"role": "user", "content": env["content"]})

                    self._ensure_identity(messages, name, role)
                    response = self._run_mate_loop(name, messages, mate_tools)

                    self._notifications.put({
                        "from": name,
                        "message": f"[Teammate '{name}' Response]\n{response}",
                    })

            # ── IDLE phase ──
            went_to_work = self._idle_phase(name, role, messages, state, mate_tools)

            if not went_to_work:
                stop_event.wait(self._check_interval)

    def _run_mate_loop(self, name: str, messages: list, mate_tools: Dict) -> str:
        """Run one Think→Act→Observe cycle for a teammate.

        Returns the final assistant response text.
        """
        tool_schemas = list(mate_tools.values()) if mate_tools else None

        for _ in range(self._max_turns):
            try:
                response = self._llm.chat(
                    messages=messages,
                    tools=tool_schemas,
                )
            except Exception as e:
                return f"[Error during LLM call: {e}]"

            # Record assistant message
            assistant_content = response.content or ""
            messages.append({"role": "assistant", "content": assistant_content})

            # If no tool calls, we're done
            if not response.tool_calls:
                return assistant_content

            # Execute tool calls
            for tc in response.tool_calls:
                tool_name = tc.name
                tool_args = tc.arguments or {}

                if tool_name not in mate_tools:
                    obs = f"Error: Unknown tool '{tool_name}'"
                else:
                    try:
                        obs = self._registry.call(tool_name, tool_args)
                    except Exception as e:
                        obs = f"Error: {e}"

                messages.append({"role": "tool", "content": str(obs)})

        # Max turns exceeded — return last assistant message
        return assistant_content or "[Teammate reached max turns]"

    def _build_mate_tools(self) -> Dict[str, Dict]:
        """Build read-only tool subset for teammates."""
        schemas = {
            schema["function"]["name"]: schema
            for schema in self._registry.get_tool_schemas()
        }
        return {
            name: schemas[name]
            for name in self._tool_whitelist
            if name in schemas
        }

    def _idle_phase(
        self,
        name: str,
        role: str,
        messages: list,
        state: Dict,
        mate_tools: Dict,
    ) -> bool:
        """IDLE phase: check inbox (priority) then scan task board.

        Returns True if the teammate transitioned to WORK (claimed a task
        or received a message), False if idle ended with nothing found.
        """
        stop_event = state["stop_event"]

        for _ in range(self._idle_max_checks):
            if stop_event.is_set():
                return False

            # Priority 1: check inbox for new messages (don't consume)
            with self._inbox_lock:
                has_messages = _inbox_has_messages(self._team_dir, name)
            if has_messages:
                return True  # Next WORK phase will handle them

            # Priority 2: scan task board for claimable tasks
            if self._task_board:
                claimable = self._task_board.scan_unclaimed(role=role)
                if claimable:
                    task = claimable[0]
                    try:
                        self._task_board.claim_task(
                            task["id"], owner=name, role=role, source="auto",
                        )
                    except ValueError:
                        # Race condition: another mate claimed it first
                        stop_event.wait(self._idle_check_interval)
                        continue

                    task_prompt = (
                        f"[Auto-assigned Task #{task['id']}]\n"
                        f"Subject: {task['subject']}\n"
                        f"Description: {task.get('description', '')}\n"
                        f"Please complete this task now."
                    )
                    self._ensure_identity(messages, name, role)
                    messages.append({"role": "user", "content": task_prompt})
                    response = self._run_mate_loop(name, messages, mate_tools)
                    self._notifications.put({
                        "from": name,
                        "message": (
                            f"[Teammate '{name}' auto-claimed task #{task['id']}]\n"
                            f"Subject: {task['subject']}\n{response}"
                        ),
                    })
                    return True

            # Nothing found — wait before next idle check
            stop_event.wait(self._idle_check_interval)

        # Exhausted idle checks without finding work
        return False

    def _ensure_identity(self, messages: list, name: str, role: str):
        """Re-inject identity block if missing (after compression/summarization).

        Checks if the first message is a system message with the teammate's
        identity. If not, prepends one.
        """
        identity_marker = f"You are '{name}'"
        if messages and messages[0].get("role") == "system":
            content = messages[0].get("content", "")
            if identity_marker in content:
                return

        messages.insert(0, _make_system_message(name, role))

    def _handle_protocol(self, name: str, envelope: Dict, state: Dict):
        """Process protocol envelopes received by a teammate.

        Handles:
            shutdown_request      → auto-approve, set stop event
            plan_approval_response → update status, feed into conversation
        """
        msg_type = envelope.get("type", "")
        request_id = envelope.get("request_id", "")

        if msg_type == "shutdown_request":
            try:
                self._tracker.update_status(request_id, "approved")
            except KeyError:
                pass
            state["stop_event"].set()
            self._notifications.put({
                "from": name,
                "message": f"[Teammate '{name}' accepted shutdown request {request_id}]",
            })

        elif msg_type == "plan_approval_response":
            payload = envelope.get("payload", {})
            approved = payload.get("approved", False)
            feedback = payload.get("feedback", "")
            status_str = "approved" if approved else "rejected"
            try:
                self._tracker.update_status(request_id, status_str, feedback=feedback)
            except KeyError:
                pass
            state["messages"].append({
                "role": "user",
                "content": f"[Plan Review] Your plan was {status_str}. Feedback: {feedback}",
            })


# ── Helpers ──


def _make_system_message(name: str, role: str) -> Dict:
    return {
        "role": "system",
        "content": (
            f"You are '{name}', a {role} on the team. "
            "You receive tasks via inbox messages. "
            "Use the available tools to complete your tasks, then respond with your findings."
        ),
    }


# ── Tool registration ──


def register_team_tools(
    registry: BaseToolRegistry,
    tm: TeammateManager,
    *,
    tool_prefix: str = "",
) -> List[str]:
    """Register team_spawn, team_send, team_list, team_shutdown."""

    def team_spawn(args: Dict) -> str:
        name = args.get("name", "").strip()
        role = args.get("role", "assistant").strip()
        if not name:
            raise RuntimeError("name is required")
        try:
            tm.spawn(name, role=role)
            return (
                f"Spawned teammate '{name}' (role: {role}). "
                f"Use {tool_prefix}team_send to assign tasks."
            )
        except (ValueError, KeyError) as e:
            raise RuntimeError(str(e)) from e

    def team_send(args: Dict) -> str:
        name = args.get("name", "").strip()
        content = args.get("content", "").strip()
        if not name:
            raise RuntimeError("name is required")
        if not content:
            raise RuntimeError("content is required")
        try:
            tm.send(name, content)
            return f"Sent to '{name}': {content[:100]}"
        except KeyError as e:
            raise RuntimeError(str(e)) from e

    def team_list(args: Dict) -> str:
        members = tm.list()
        if not members:
            return f"No teammates. Use {tool_prefix}team_spawn to create one."
        lines = []
        for m in members:
            status = m.get("status", "unknown")
            lines.append(f"  {m['name']} [{status}] — {m['role']}")
        return "\n".join(lines)

    def team_shutdown(args: Dict) -> str:
        name = args.get("name", "").strip()
        if not name:
            raise RuntimeError("name is required")
        try:
            tm.shutdown(name)
            return f"Shutdown teammate '{name}'."
        except KeyError as e:
            raise RuntimeError(str(e)) from e

    def team_request_shutdown(args: Dict) -> str:
        target = args.get("name", "").strip()
        if not target:
            raise RuntimeError("name is required")
        try:
            record = tm.request_shutdown(target)
            return (
                f"Sent shutdown request to '{target}' (id: {record['request_id']}). "
                f"Waiting for acceptance."
            )
        except (KeyError, ValueError) as e:
            raise RuntimeError(str(e)) from e

    def team_submit_plan(args: Dict) -> str:
        name = args.get("name", "").strip()
        plan_text = args.get("plan", "").strip()
        if not name:
            raise RuntimeError("name is required")
        if not plan_text:
            raise RuntimeError("plan is required")
        record = tm.submit_plan(name, plan_text)
        return (
            f"Plan submitted for approval (id: {record['request_id']}). "
            f"Use {tool_prefix}team_review to respond."
        )

    def team_review(args: Dict) -> str:
        request_id = args.get("request_id", "").strip()
        approve = args.get("approve", True)
        feedback = args.get("feedback", "").strip()
        if not request_id:
            raise RuntimeError("request_id is required")
        try:
            result = tm.review_request(request_id, approve=approve, feedback=feedback)
            return f"Request {request_id} {result['status']}."
        except (KeyError, ValueError) as e:
            raise RuntimeError(str(e)) from e

    def team_requests(args: Dict) -> str:
        status = args.get("status", "").strip() or None
        requests = tm.list_requests(status=status)
        if not requests:
            return "No requests found."
        lines = []
        for r in requests:
            lines.append(
                f"  {r['request_id']} [{r['status']}] {r['kind']} "
                f"from={r['from']} to={r['to']}"
            )
        return "\n".join(lines)

    tools = [
        (
            "team_spawn",
            team_spawn,
            "Spawn a long-lived teammate with its own Think-Act-Observe loop. "
            "The teammate runs in the background and can receive tasks via team_send.",
            {
                "name": {"type": "string", "description": "Unique name for the teammate"},
                "role": {"type": "string", "description": "Role description (default: assistant)"},
            },
        ),
        (
            "team_send",
            team_send,
            "Send a message/task to a teammate's inbox. The teammate will process it autonomously.",
            {
                "name": {"type": "string", "description": "Teammate name"},
                "content": {"type": "string", "description": "Message or task description"},
            },
        ),
        (
            "team_list",
            team_list,
            "List all team members and their status.",
            {},
        ),
        (
            "team_shutdown",
            team_shutdown,
            "Shutdown a teammate. Stops its daemon loop.",
            {
                "name": {"type": "string", "description": "Teammate name to shutdown"},
            },
        ),
        (
            "team_request_shutdown",
            team_request_shutdown,
            "Send a graceful shutdown request to a teammate. The teammate must accept before shutting down.",
            {
                "name": {"type": "string", "description": "Teammate name to request shutdown"},
            },
        ),
        (
            "team_submit_plan",
            team_submit_plan,
            "Submit a plan for approval before executing it.",
            {
                "name": {"type": "string", "description": "Teammate name submitting the plan"},
                "plan": {"type": "string", "description": "Plan description for review"},
            },
        ),
        (
            "team_review",
            team_review,
            "Review a pending request (approve or reject).",
            {
                "request_id": {"type": "string", "description": "Request ID to review"},
                "approve": {"type": "boolean", "description": "True to approve, False to reject"},
                "feedback": {"type": "string", "description": "Optional feedback"},
            },
        ),
        (
            "team_requests",
            team_requests,
            "List tracked requests, optionally filtered by status.",
            {
                "status": {"type": "string", "description": "Filter by status (pending, approved, rejected)"},
            },
        ),
    ]

    installed = []
    for base_name, handler, desc, params in tools:
        t_name = f"{tool_prefix}{base_name}" if tool_prefix else base_name
        required = ["name"] if base_name not in ("team_list", "team_requests") else []
        if base_name == "team_send":
            required.append("content")
        if base_name == "team_submit_plan":
            required = ["name", "plan"]
        if base_name == "team_review":
            required = ["request_id"]
        registry.register(
            name=t_name,
            handler=handler,
            schema={
                "type": "function",
                "function": {
                    "name": t_name,
                    "description": desc,
                    "parameters": {
                        "type": "object",
                        "properties": params,
                        "required": required,
                    },
                },
            },
        )
        installed.append(t_name)
    return installed
