"""Task system — minimal task board with dependency tracking.

Core types:
    TaskRecord  — a single task (id, subject, status, dependencies, owner)
    TaskStatus  — 4 states: pending → in_progress → completed | deleted
    TaskBoard   — CRUD + ready logic + JSON persistence

The central rule:
    is_ready(task) = task.status == "pending" and not task.blockedBy
"""

import json
import os
import threading
import time
from enum import Enum
from typing import Any, Dict, List, Optional

from app.dispatch import DispatchRegistry, inprocess_handler, tool_result


# ── TaskStatus ──


class TaskStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    DELETED = "deleted"


# ── TaskRecord ──


def _new_id() -> int:
    """Generate a monotonically increasing task ID."""
    _new_id._counter += 1
    return _new_id._counter


_new_id._counter = 0


def make_task(
    subject: str,
    description: str = "",
    blockedBy: Optional[List[int]] = None,
    blocks: Optional[List[int]] = None,
    owner: str = "",
    status: str = TaskStatus.PENDING,
    task_id: Optional[int] = None,
) -> Dict[str, Any]:
    """Create a task record as a plain dict."""
    return {
        "id": task_id if task_id is not None else _new_id(),
        "subject": subject,
        "description": description,
        "status": status,
        "blockedBy": blockedBy or [],
        "blocks": blocks or [],
        "owner": owner,
    }


# ── Ready Rule ──


def is_ready(task: Dict[str, Any]) -> bool:
    """A task is ready when it's pending and nothing blocks it."""
    return task["status"] == TaskStatus.PENDING and not task["blockedBy"]


def _task_allows_role(task: Dict[str, Any], role: Optional[str]) -> bool:
    """Check if a role is allowed to claim this task."""
    required = task.get("required_role")
    if required is None:
        return True
    if role is None:
        return False
    return required.lower() in role.lower()


def is_claimable(task: Dict[str, Any], role: Optional[str] = None) -> bool:
    """A task is claimable when pending, unowned, unblocked, and role-matches."""
    if task["status"] != TaskStatus.PENDING:
        return False
    if task.get("owner"):
        return False
    if task.get("blockedBy"):
        return False
    if not _task_allows_role(task, role):
        return False
    return True


# ── TaskBoard ──


class TaskBoard:
    """In-memory task board with JSON persistence.

    Usage:
        board = TaskBoard()
        board.add("Write parser")
        board.add("Write tests", blocked_by=[1])
        ready = board.ready()      # only task 1
        board.start(1)
        board.complete(1)
        ready = board.ready()      # now task 2 is unblocked
    """

    def __init__(self, persist_path: Optional[str] = None):
        self._tasks: Dict[int, Dict[str, Any]] = {}
        self._next_id: int = 1
        self._persist_path = persist_path
        self._lock = threading.Lock()
        self._claim_events_path = None
        if persist_path:
            self._claim_events_path = os.path.join(
                os.path.dirname(persist_path), "claim_events.jsonl"
            )
        if persist_path and os.path.exists(persist_path):
            self._load()

    # ── CRUD ──

    def add(
        self,
        subject: str,
        description: str = "",
        blocked_by: Optional[List[int]] = None,
        owner: str = "",
        required_role: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Create a new task and register reverse dependency links."""
        task_id = self._next_id
        self._next_id += 1

        blocked_by = blocked_by or []
        # Validate dependencies exist
        for dep_id in blocked_by:
            if dep_id not in self._tasks:
                raise KeyError(f"Dependency task {dep_id} does not exist")

        task = {
            "id": task_id,
            "subject": subject,
            "description": description,
            "status": TaskStatus.PENDING,
            "blockedBy": blocked_by,
            "blocks": [],
            "owner": owner,
            "required_role": required_role,
            "claim_role": None,
            "claimed_at": None,
            "claim_source": None,
            "worktree": None,
            "worktree_state": "unbound",
            "last_worktree": None,
            "closeout": None,
        }
        self._tasks[task_id] = task

        # Register reverse links: blocker → this task
        for dep_id in blocked_by:
            self._tasks[dep_id]["blocks"].append(task_id)

        self._save()
        return task

    def get(self, task_id: int) -> Optional[Dict[str, Any]]:
        """Get a task by ID. Returns None if not found."""
        return self._tasks.get(task_id)

    def update(
        self,
        task_id: int,
        subject: Optional[str] = None,
        description: Optional[str] = None,
        owner: Optional[str] = None,
        add_blocked_by: Optional[List[int]] = None,
        add_blocks: Optional[List[int]] = None,
    ) -> Dict[str, Any]:
        """Update mutable fields of a task (not status — use start/complete/delete)."""
        task = self._require(task_id)

        if subject is not None:
            task["subject"] = subject
        if description is not None:
            task["description"] = description
        if owner is not None:
            task["owner"] = owner
        if add_blocked_by:
            for dep_id in add_blocked_by:
                if dep_id not in self._tasks:
                    raise KeyError(f"Dependency task {dep_id} does not exist")
                if dep_id not in task["blockedBy"]:
                    task["blockedBy"].append(dep_id)
                    self._tasks[dep_id]["blocks"].append(task_id)
        if add_blocks:
            for blocked_id in add_blocks:
                if blocked_id not in self._tasks:
                    raise KeyError(f"Blocked task {blocked_id} does not exist")
                if blocked_id not in task["blocks"]:
                    task["blocks"].append(blocked_id)
                    self._tasks[blocked_id]["blockedBy"].append(task_id)

        self._save()
        return task

    # ── Status transitions ──

    def start(self, task_id: int, owner: str = "") -> Dict[str, Any]:
        """Mark a task as in_progress. Only pending tasks can be started."""
        task = self._require(task_id)
        if task["status"] != TaskStatus.PENDING:
            raise ValueError(f"Task {task_id} is {task['status']}, cannot start")
        task["status"] = TaskStatus.IN_PROGRESS
        if owner:
            task["owner"] = owner
        self._save()
        return task

    def complete(self, task_id: int) -> Dict[str, Any]:
        """Complete a task and unblock all tasks waiting on it."""
        task = self._require(task_id)
        if task["status"] not in (TaskStatus.PENDING, TaskStatus.IN_PROGRESS):
            raise ValueError(f"Task {task_id} is {task['status']}, cannot complete")
        task["status"] = TaskStatus.COMPLETED

        # Unblock dependents: remove this task from their blockedBy
        for blocked_id in task["blocks"]:
            blocked = self._tasks.get(blocked_id)
            if blocked and task_id in blocked["blockedBy"]:
                blocked["blockedBy"].remove(task_id)

        self._save()
        return task

    def delete(self, task_id: int) -> Dict[str, Any]:
        """Logical delete — task stays in board but is marked deleted."""
        task = self._require(task_id)
        task["status"] = TaskStatus.DELETED

        # Remove this task from blockedBy of tasks it blocks
        for blocked_id in task["blocks"]:
            blocked = self._tasks.get(blocked_id)
            if blocked and task_id in blocked["blockedBy"]:
                blocked["blockedBy"].remove(task_id)

        # Remove this task from blocks of tasks it was blocked by
        for blocker_id in task["blockedBy"]:
            blocker = self._tasks.get(blocker_id)
            if blocker and task_id in blocker["blocks"]:
                blocker["blocks"].remove(task_id)

        self._save()
        return task

    # ── Claim ──

    def claim_task(
        self,
        task_id: int,
        owner: str,
        role: Optional[str] = None,
        source: str = "auto",
    ) -> Dict[str, Any]:
        """Atomically claim a task for a teammate.

        Sets owner, status=in_progress, and claim metadata.
        Appends a claim event to the audit log.
        Raises ValueError if task is not claimable.
        """
        with self._lock:
            task = self._require(task_id)
            if not is_claimable(task, role):
                raise ValueError(
                    f"Task {task_id} is not claimable "
                    f"(status={task['status']}, owner={task.get('owner')}, "
                    f"blockedBy={task.get('blockedBy')}, "
                    f"required_role={task.get('required_role')})"
                )
            task["owner"] = owner
            task["status"] = TaskStatus.IN_PROGRESS
            task["claim_role"] = role
            task["claimed_at"] = time.time()
            task["claim_source"] = source
            self._save()
            self._append_claim_event(task, owner, role, source)
        return task

    def _append_claim_event(self, task, owner, role, source):
        """Append a claim event to the JSONL audit log."""
        if not self._claim_events_path:
            return
        event = {
            "event": "claimed",
            "task_id": task["id"],
            "owner": owner,
            "role": role,
            "source": source,
            "ts": time.time(),
        }
        os.makedirs(os.path.dirname(self._claim_events_path), exist_ok=True)
        with open(self._claim_events_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")

    def scan_unclaimed(self, role: Optional[str] = None) -> List[Dict[str, Any]]:
        """Return all tasks claimable by the given role, ordered by ID."""
        return [
            t for t in sorted(self._tasks.values(), key=lambda t: t["id"])
            if is_claimable(t, role)
        ]

    # ── Queries ──

    def ready(self) -> List[Dict[str, Any]]:
        """Return all tasks that are ready to start."""
        return [t for t in self._tasks.values() if is_ready(t)]

    def list(
        self,
        status: Optional[str] = None,
        owner: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """List tasks with optional filters."""
        tasks = list(self._tasks.values())
        if status:
            tasks = [t for t in tasks if t["status"] == status]
        if owner:
            tasks = [t for t in tasks if t["owner"] == owner]
        return tasks

    def summary(self) -> str:
        """Human-readable board summary."""
        all_tasks = self.list()
        active = [t for t in all_tasks if t["status"] != TaskStatus.DELETED]
        by_status = {}
        for t in active:
            by_status.setdefault(t["status"], []).append(t)

        lines = [f"Total: {len(active)} tasks"]
        for status in (TaskStatus.PENDING, TaskStatus.IN_PROGRESS, TaskStatus.COMPLETED):
            count = len(by_status.get(status, []))
            if count:
                lines.append(f"  {status}: {count}")

        ready_tasks = self.ready()
        if ready_tasks:
            lines.append(f"\nReady to start:")
            for t in ready_tasks:
                lines.append(f"  #{t['id']} {t['subject']}")

        in_progress = by_status.get(TaskStatus.IN_PROGRESS, [])
        if in_progress:
            lines.append(f"\nIn progress:")
            for t in in_progress:
                lines.append(f"  #{t['id']} {t['subject']} (owner: {t['owner'] or 'unassigned'})")

        return "\n".join(lines)

    # ── Worktree binding ──

    def bind_worktree(self, task_id: int, name: str):
        """Bind a worktree to a task.

        Sets worktree, last_worktree, worktree_state='active'.
        If task is pending, also sets status to in_progress.
        """
        task = self._require(task_id)
        task["worktree"] = name
        task["last_worktree"] = name
        task["worktree_state"] = "active"
        if task["status"] == TaskStatus.PENDING:
            task["status"] = TaskStatus.IN_PROGRESS
        self._save()

    def unbind_worktree(self, task_id: int, action: str, closeout_record: dict):
        """Unbind a worktree from a task after closeout.

        Sets worktree=None, worktree_state to 'kept'/'removed', stores closeout.
        """
        task = self._require(task_id)
        task["worktree"] = None
        # Map closeout action to past-tense state: keep→kept, remove→removed
        task["worktree_state"] = "kept" if action == "keep" else "removed"
        task["closeout"] = closeout_record
        self._save()

    # ── Internals ──

    def _require(self, task_id: int) -> Dict[str, Any]:
        task = self._tasks.get(task_id)
        if task is None:
            raise KeyError(f"Task {task_id} not found")
        return task

    def _save(self):
        if not self._persist_path:
            return
        data = {
            "next_id": self._next_id,
            "tasks": {str(k): v for k, v in self._tasks.items()},
        }
        tmp = self._persist_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp, self._persist_path)

    def _load(self):
        with open(self._persist_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        self._next_id = data.get("next_id", 1)
        self._tasks = {int(k): v for k, v in data.get("tasks", {}).items()}


# ── Tool registration ──


def register_task_tools(registry: DispatchRegistry, board: TaskBoard):
    """Register task_create, task_list, task_update, task_complete tools."""

    def task_create(args: Dict) -> tool_result:
        subject = args.get("subject", "")
        if not subject:
            return tool_result(ok=False, output="", error="subject is required")

        blocked_by = args.get("blockedBy") or []
        if isinstance(blocked_by, str):
            blocked_by = [int(x) for x in blocked_by.split(",") if x.strip()]

        try:
            task = board.add(
                subject=subject,
                description=args.get("description", ""),
                blocked_by=blocked_by,
                owner=args.get("owner", ""),
                required_role=args.get("required_role"),
            )
            return tool_result(
                ok=True,
                output=f"Created task #{task['id']}: {task['subject']} [{task['status']}]"
                       + (f"\n  blockedBy: {task['blockedBy']}" if task["blockedBy"] else ""),
            )
        except KeyError as e:
            return tool_result(ok=False, output="", error=str(e))

    def task_list(args: Dict) -> tool_result:
        status = args.get("status")
        owner = args.get("owner")
        tasks = board.list(status=status, owner=owner)
        if not tasks:
            return tool_result(ok=True, output="No tasks found.")

        lines = []
        for t in tasks:
            blocker_info = f" (blocked by {t['blockedBy']})" if t["blockedBy"] else ""
            owner_info = f" [{t['owner']}]" if t["owner"] else ""
            lines.append(
                f"  #{t['id']} [{t['status']}] {t['subject']}{owner_info}{blocker_info}"
            )
        return tool_result(ok=True, output="\n".join(lines))

    def task_update(args: Dict) -> tool_result:
        task_id = args.get("id")
        if task_id is None:
            return tool_result(ok=False, output="", error="id is required")

        try:
            task = board.update(
                task_id=int(task_id),
                subject=args.get("subject"),
                description=args.get("description"),
                owner=args.get("owner"),
                add_blocked_by=args.get("addBlockedBy"),
                add_blocks=args.get("addBlocks"),
            )
            return tool_result(
                ok=True,
                output=f"Updated task #{task['id']}: {task['subject']}",
            )
        except (KeyError, ValueError) as e:
            return tool_result(ok=False, output="", error=str(e))

    def task_complete(args: Dict) -> tool_result:
        task_id = args.get("id")
        if task_id is None:
            return tool_result(ok=False, output="", error="id is required")

        try:
            task = board.complete(int(task_id))
            # Report what got unblocked
            unblocked = [
                f"#{bid}" for bid in task["blocks"]
                if is_ready(board.get(bid))
            ]
            msg = f"Completed task #{task['id']}: {task['subject']}"
            if unblocked:
                msg += f"\n  Unblocked: {', '.join(unblocked)}"
            return tool_result(ok=True, output=msg)
        except (KeyError, ValueError) as e:
            return tool_result(ok=False, output="", error=str(e))

    # Register all four tools
    for name, handler, desc, params in [
        (
            "task_create",
            task_create,
            "Create a new task on the task board. Tasks track work items with dependency chains.",
            {
                "subject": {"type": "string", "description": "One-line task title"},
                "description": {"type": "string", "description": "Detailed description or notes"},
                "blockedBy": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": "IDs of tasks that must complete before this one can start",
                },
                "owner": {"type": "string", "description": "Who is responsible for this task"},
                "required_role": {"type": "string", "description": "Role required to auto-claim this task. None means any role can claim."},
            },
        ),
        (
            "task_list",
            task_list,
            "List tasks on the board. Optionally filter by status or owner.",
            {
                "status": {
                    "type": "string",
                    "enum": ["pending", "in_progress", "completed", "deleted"],
                    "description": "Filter by status",
                },
                "owner": {"type": "string", "description": "Filter by owner"},
            },
        ),
        (
            "task_update",
            task_update,
            "Update a task's subject, description, owner, or dependencies.",
            {
                "id": {"type": "integer", "description": "Task ID to update"},
                "subject": {"type": "string", "description": "New subject"},
                "description": {"type": "string", "description": "New description"},
                "owner": {"type": "string", "description": "New owner"},
                "addBlockedBy": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": "Add task IDs that block this task",
                },
                "addBlocks": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": "Add task IDs that this task blocks",
                },
            },
        ),
        (
            "task_complete",
            task_complete,
            "Mark a task as completed. Automatically unblocks dependent tasks.",
            {
                "id": {"type": "integer", "description": "Task ID to complete"},
            },
        ),
    ]:
        registry.register(
            name=name,
            handler=handler,
            schema={
                "type": "function",
                "function": {
                    "name": name,
                    "description": desc,
                    "parameters": {
                        "type": "object",
                        "properties": params,
                        "required": ["subject"] if name == "task_create" else (["id"] if name in ("task_update", "task_complete") else []),
                    },
                },
            },
        )
