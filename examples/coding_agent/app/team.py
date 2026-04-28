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
import threading
import time
from typing import Any, Dict, List, Optional

from app.dispatch import DispatchRegistry, inprocess_handler, tool_result


# ── Defaults ──

_TEAM_DIR = ".team"
_CONFIG_FILE = "config.json"
_INBOX_DIR = "inbox"
_MATE_MAX_TURNS = 8
_MATE_CHECK_INTERVAL = 5  # seconds between inbox checks
_REQUESTS_DIR = "requests"


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
        path = os.path.join(self._dir, f"{request_id}.json")
        if not os.path.exists(path):
            return None
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    def update_status(self, request_id: str, status: str, feedback: str = None) -> Dict:
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
        registry: DispatchRegistry,
        workspace_root: str,
        team_dir: Optional[str] = None,
    ):
        self._llm = llm_client
        self._registry = registry
        self._workspace_root = workspace_root
        self._team_dir = team_dir or os.path.join(workspace_root, _TEAM_DIR)
        self._notifications: queue.Queue = queue.Queue()
        self._lock = threading.Lock()
        self._tracker = RequestTracker(self._team_dir)

        # Active teammate state: {name: {"thread", "stop_event", "messages"}}
        self._mates: Dict[str, Dict[str, Any]] = {}

        os.makedirs(os.path.join(self._team_dir, _INBOX_DIR), exist_ok=True)

    def spawn(self, name: str, role: str = "assistant") -> Dict[str, Any]:
        """Spawn a new teammate.

        Creates a roster entry and starts the teammate's daemon loop.
        """
        with self._lock:
            if name in self._mates:
                raise ValueError(f"Teammate '{name}' already active")

        # Update roster
        roster = _load_roster(self._team_dir)
        if _roster_member(roster, name):
            raise ValueError(f"Teammate '{name}' already in roster")

        member = {"name": name, "role": role, "status": "active", "created_at": time.time()}
        roster["members"].append(member)
        _save_roster(self._team_dir, roster)

        # Start daemon loop
        stop_event = threading.Event()
        mate_state = {
            "thread": None,
            "stop_event": stop_event,
            "messages": [_make_system_message(name, role)],
        }

        t = threading.Thread(
            target=self._mate_loop,
            args=(name, mate_state),
            daemon=True,
            name=f"mate-{name}",
        )
        mate_state["thread"] = t
        with self._lock:
            self._mates[name] = mate_state
        t.start()

        return member

    def send(self, name: str, content: str, from_name: str = "lead") -> Dict[str, Any]:
        """Send a message to a teammate's inbox."""
        with self._lock:
            if name not in self._mates:
                raise KeyError(f"Teammate '{name}' not found")

        envelope = _make_envelope(from_name, content)
        _send_to_inbox(self._team_dir, name, envelope)
        return {"to": name, "status": "sent", "content_preview": content[:100]}

    def shutdown(self, name: str) -> Dict[str, Any]:
        """Shutdown a teammate. Stops the daemon loop and updates roster."""
        with self._lock:
            mate = self._mates.pop(name, None)
            if mate is None:
                raise KeyError(f"Teammate '{name}' not found")
            mate["stop_event"].set()

        # Update roster
        roster = _load_roster(self._team_dir)
        member = _roster_member(roster, name)
        if member:
            member["status"] = "shutdown"
            _save_roster(self._team_dir, roster)

        return {"name": name, "status": "shutdown"}

    def list(self) -> List[Dict[str, Any]]:
        """List all team members from the roster."""
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
        """Shutdown all active teammates."""
        with self._lock:
            for name, mate in list(self._mates.items()):
                mate["stop_event"].set()
            self._mates.clear()

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
                _send_to_inbox(self._team_dir, target, envelope)

        return {"request_id": request_id, "status": status}

    def list_requests(self, status: str = None) -> List[Dict]:
        """List tracked requests, optionally filtered by status."""
        return self._tracker.list(status=status)

    # ── Teammate loop ──

    def _mate_loop(self, name: str, state: Dict):
        """Daemon loop for a single teammate.

        Every _MATE_CHECK_INTERVAL seconds:
            1. Drain inbox → append to own messages
            2. If new messages: run Think→Act→Observe
            3. Put response into notification queue
        """
        stop_event = state["stop_event"]
        messages = state["messages"]

        # Build tool subset for teammate (read-only)
        mate_tools = self._build_mate_tools()

        while not stop_event.is_set():
            # 1. Drain inbox
            inbox_msgs = _read_inbox(self._team_dir, name)
            if not inbox_msgs:
                stop_event.wait(_MATE_CHECK_INTERVAL)
                continue

            # 2. Separate protocol from regular messages
            protocol_msgs = [env for env in inbox_msgs if env.get("request_id")]
            regular_msgs = [env for env in inbox_msgs if not env.get("request_id")]

            # 3. Handle protocol messages
            for env in protocol_msgs:
                self._handle_protocol(name, env, state)
                if stop_event.is_set():
                    return  # shutdown handled — exit loop

            # 4. Handle regular messages with LLM
            if regular_msgs:
                for env in regular_msgs:
                    messages.append({"role": "user", "content": env["content"]})

                response = self._run_mate_loop(name, messages, mate_tools)

                self._notifications.put({
                    "from": name,
                    "message": f"[Teammate '{name}' Response]\n{response}",
                })

            stop_event.wait(_MATE_CHECK_INTERVAL)

    def _run_mate_loop(self, name: str, messages: list, mate_tools: Dict) -> str:
        """Run one Think→Act→Observe cycle for a teammate.

        Returns the final assistant response text.
        """
        tool_schemas = list(mate_tools.values()) if mate_tools else None

        for _ in range(_MATE_MAX_TURNS):
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
        read_only = {"file_read", "file_list", "file_find", "search_code", "list_files"}
        schemas = self._registry.schemas
        return {name: schemas[name] for name in read_only if name in schemas}

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


def register_team_tools(registry: DispatchRegistry, tm: TeammateManager):
    """Register team_spawn, team_send, team_list, team_shutdown."""

    def team_spawn(args: Dict) -> tool_result:
        name = args.get("name", "").strip()
        role = args.get("role", "assistant").strip()
        if not name:
            return tool_result(ok=False, output="", error="name is required")
        try:
            member = tm.spawn(name, role=role)
            return tool_result(
                ok=True,
                output=f"Spawned teammate '{name}' (role: {role}). "
                       f"Use team_send to assign tasks.",
            )
        except (ValueError, KeyError) as e:
            return tool_result(ok=False, output="", error=str(e))

    def team_send(args: Dict) -> tool_result:
        name = args.get("name", "").strip()
        content = args.get("content", "").strip()
        if not name:
            return tool_result(ok=False, output="", error="name is required")
        if not content:
            return tool_result(ok=False, output="", error="content is required")
        try:
            result = tm.send(name, content)
            return tool_result(ok=True, output=f"Sent to '{name}': {content[:100]}")
        except KeyError as e:
            return tool_result(ok=False, output="", error=str(e))

    def team_list(args: Dict) -> tool_result:
        members = tm.list()
        if not members:
            return tool_result(ok=True, output="No teammates. Use team_spawn to create one.")
        lines = []
        for m in members:
            status = m.get("status", "unknown")
            lines.append(f"  {m['name']} [{status}] — {m['role']}")
        return tool_result(ok=True, output="\n".join(lines))

    def team_shutdown(args: Dict) -> tool_result:
        name = args.get("name", "").strip()
        if not name:
            return tool_result(ok=False, output="", error="name is required")
        try:
            result = tm.shutdown(name)
            return tool_result(ok=True, output=f"Shutdown teammate '{name}'.")
        except KeyError as e:
            return tool_result(ok=False, output="", error=str(e))

    def team_request_shutdown(args: Dict) -> tool_result:
        target = args.get("name", "").strip()
        if not target:
            return tool_result(ok=False, output="", error="name is required")
        try:
            record = tm.request_shutdown(target)
            return tool_result(
                ok=True,
                output=f"Sent shutdown request to '{target}' (id: {record['request_id']}). "
                       f"Waiting for acceptance.",
            )
        except (KeyError, ValueError) as e:
            return tool_result(ok=False, output="", error=str(e))

    def team_submit_plan(args: Dict) -> tool_result:
        name = args.get("name", "").strip()
        plan_text = args.get("plan", "").strip()
        if not name:
            return tool_result(ok=False, output="", error="name is required")
        if not plan_text:
            return tool_result(ok=False, output="", error="plan is required")
        record = tm.submit_plan(name, plan_text)
        return tool_result(
            ok=True,
            output=f"Plan submitted for approval (id: {record['request_id']}). "
                   f"Use team_review to respond.",
        )

    def team_review(args: Dict) -> tool_result:
        request_id = args.get("request_id", "").strip()
        approve = args.get("approve", True)
        feedback = args.get("feedback", "").strip()
        if not request_id:
            return tool_result(ok=False, output="", error="request_id is required")
        try:
            result = tm.review_request(request_id, approve=approve, feedback=feedback)
            return tool_result(
                ok=True,
                output=f"Request {request_id} {result['status']}.",
            )
        except (KeyError, ValueError) as e:
            return tool_result(ok=False, output="", error=str(e))

    def team_requests(args: Dict) -> tool_result:
        status = args.get("status", "").strip() or None
        requests = tm.list_requests(status=status)
        if not requests:
            return tool_result(ok=True, output="No requests found.")
        lines = []
        for r in requests:
            lines.append(
                f"  {r['request_id']} [{r['status']}] {r['kind']} "
                f"from={r['from']} to={r['to']}"
            )
        return tool_result(ok=True, output="\n".join(lines))

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

    for t_name, handler, desc, params in tools:
        required = ["name"] if t_name not in ("team_list", "team_requests") else []
        if t_name == "team_send":
            required.append("content")
        if t_name == "team_submit_plan":
            required = ["name", "plan"]
        if t_name == "team_review":
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
