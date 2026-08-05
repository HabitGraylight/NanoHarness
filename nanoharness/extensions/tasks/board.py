"""Thread-safe task board with dependencies, claims, and JSON persistence."""

import json
import os
import threading
import time
from enum import Enum
from typing import Any, Dict, List, Optional


class TaskStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    DELETED = "deleted"


def _new_id() -> int:
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
    return {
        "id": task_id if task_id is not None else _new_id(),
        "subject": subject,
        "description": description,
        "status": status,
        "blockedBy": blockedBy or [],
        "blocks": blocks or [],
        "owner": owner,
    }


def is_ready(task: Dict[str, Any]) -> bool:
    return task["status"] == TaskStatus.PENDING and not task["blockedBy"]


def _task_allows_role(task: Dict[str, Any], role: Optional[str]) -> bool:
    required = task.get("required_role")
    if required is None:
        return True
    if role is None:
        return False
    return required.lower() in role.lower()


def is_claimable(task: Dict[str, Any], role: Optional[str] = None) -> bool:
    return (
        task["status"] == TaskStatus.PENDING
        and not task.get("owner")
        and not task.get("blockedBy")
        and _task_allows_role(task, role)
    )


class TaskBoard:
    """Task CRUD, dependency transitions, claims, and worktree bindings."""

    def __init__(self, persist_path: Optional[str] = None):
        self._tasks: Dict[int, Dict[str, Any]] = {}
        self._next_id = 1
        self._persist_path = persist_path
        self._lock = threading.RLock()
        self._claim_events_path = None
        if persist_path:
            directory = os.path.dirname(persist_path)
            if directory:
                os.makedirs(directory, exist_ok=True)
            self._claim_events_path = os.path.join(directory, "claim_events.jsonl")
            if os.path.exists(persist_path):
                self._load()

    def add(
        self,
        subject: str,
        description: str = "",
        blocked_by: Optional[List[int]] = None,
        owner: str = "",
        required_role: Optional[str] = None,
    ) -> Dict[str, Any]:
        with self._lock:
            dependencies = list(blocked_by or [])
            for dependency_id in dependencies:
                if dependency_id not in self._tasks:
                    raise KeyError(
                        f"Dependency task {dependency_id} does not exist"
                    )
            task_id = self._next_id
            self._next_id += 1
            task = {
                "id": task_id,
                "subject": subject,
                "description": description,
                "status": TaskStatus.PENDING,
                "blockedBy": dependencies,
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
            for dependency_id in dependencies:
                self._tasks[dependency_id]["blocks"].append(task_id)
            self._save()
            return task

    def get(self, task_id: int) -> Optional[Dict[str, Any]]:
        with self._lock:
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
        with self._lock:
            task = self._require(task_id)
            if subject is not None:
                task["subject"] = subject
            if description is not None:
                task["description"] = description
            if owner is not None:
                task["owner"] = owner
            for dependency_id in add_blocked_by or []:
                if dependency_id not in self._tasks:
                    raise KeyError(
                        f"Dependency task {dependency_id} does not exist"
                    )
                if dependency_id not in task["blockedBy"]:
                    task["blockedBy"].append(dependency_id)
                    self._tasks[dependency_id]["blocks"].append(task_id)
            for blocked_id in add_blocks or []:
                if blocked_id not in self._tasks:
                    raise KeyError(f"Blocked task {blocked_id} does not exist")
                if blocked_id not in task["blocks"]:
                    task["blocks"].append(blocked_id)
                    self._tasks[blocked_id]["blockedBy"].append(task_id)
            self._save()
            return task

    def start(self, task_id: int, owner: str = "") -> Dict[str, Any]:
        with self._lock:
            task = self._require(task_id)
            if task["status"] != TaskStatus.PENDING:
                raise ValueError(
                    f"Task {task_id} is {task['status']}, cannot start"
                )
            task["status"] = TaskStatus.IN_PROGRESS
            if owner:
                task["owner"] = owner
            self._save()
            return task

    def complete(self, task_id: int) -> Dict[str, Any]:
        with self._lock:
            task = self._require(task_id)
            if task["status"] not in (
                TaskStatus.PENDING,
                TaskStatus.IN_PROGRESS,
            ):
                raise ValueError(
                    f"Task {task_id} is {task['status']}, cannot complete"
                )
            task["status"] = TaskStatus.COMPLETED
            for blocked_id in task["blocks"]:
                blocked = self._tasks.get(blocked_id)
                if blocked and task_id in blocked["blockedBy"]:
                    blocked["blockedBy"].remove(task_id)
            self._save()
            return task

    def delete(self, task_id: int) -> Dict[str, Any]:
        with self._lock:
            task = self._require(task_id)
            task["status"] = TaskStatus.DELETED
            for blocked_id in task["blocks"]:
                blocked = self._tasks.get(blocked_id)
                if blocked and task_id in blocked["blockedBy"]:
                    blocked["blockedBy"].remove(task_id)
            for blocker_id in task["blockedBy"]:
                blocker = self._tasks.get(blocker_id)
                if blocker and task_id in blocker["blocks"]:
                    blocker["blocks"].remove(task_id)
            self._save()
            return task

    def claim_task(
        self,
        task_id: int,
        owner: str,
        role: Optional[str] = None,
        source: str = "auto",
    ) -> Dict[str, Any]:
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

    def scan_unclaimed(self, role: Optional[str] = None) -> List[Dict[str, Any]]:
        with self._lock:
            return [
                task
                for task in sorted(
                    self._tasks.values(), key=lambda item: item["id"]
                )
                if is_claimable(task, role)
            ]

    def ready(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [task for task in self._tasks.values() if is_ready(task)]

    def list(
        self,
        status: Optional[str] = None,
        owner: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        with self._lock:
            tasks = list(self._tasks.values())
            if status:
                tasks = [task for task in tasks if task["status"] == status]
            if owner:
                tasks = [task for task in tasks if task["owner"] == owner]
            return tasks

    def summary(self) -> str:
        all_tasks = self.list()
        active = [
            task
            for task in all_tasks
            if task["status"] != TaskStatus.DELETED
        ]
        by_status: Dict[str, List[Dict[str, Any]]] = {}
        for task in active:
            by_status.setdefault(task["status"], []).append(task)
        lines = [f"Total: {len(active)} tasks"]
        for status in (
            TaskStatus.PENDING,
            TaskStatus.IN_PROGRESS,
            TaskStatus.COMPLETED,
        ):
            count = len(by_status.get(status, []))
            if count:
                lines.append(f"  {status}: {count}")
        ready_tasks = self.ready()
        if ready_tasks:
            lines.append("\nReady to start:")
            for task in ready_tasks:
                lines.append(f"  #{task['id']} {task['subject']}")
        in_progress = by_status.get(TaskStatus.IN_PROGRESS, [])
        if in_progress:
            lines.append("\nIn progress:")
            for task in in_progress:
                lines.append(
                    f"  #{task['id']} {task['subject']} "
                    f"(owner: {task['owner'] or 'unassigned'})"
                )
        return "\n".join(lines)

    def bind_worktree(self, task_id: int, name: str) -> None:
        with self._lock:
            task = self._require(task_id)
            task["worktree"] = name
            task["last_worktree"] = name
            task["worktree_state"] = "active"
            if task["status"] == TaskStatus.PENDING:
                task["status"] = TaskStatus.IN_PROGRESS
            self._save()

    def unbind_worktree(
        self,
        task_id: int,
        action: str,
        closeout_record: dict,
    ) -> None:
        with self._lock:
            task = self._require(task_id)
            task["worktree"] = None
            task["worktree_state"] = (
                "kept" if action == "keep" else "removed"
            )
            task["closeout"] = closeout_record
            self._save()

    def _require(self, task_id: int) -> Dict[str, Any]:
        task = self._tasks.get(task_id)
        if task is None:
            raise KeyError(f"Task {task_id} not found")
        return task

    def _append_claim_event(
        self,
        task: Dict[str, Any],
        owner: str,
        role: Optional[str],
        source: str,
    ) -> None:
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
        with open(self._claim_events_path, "a", encoding="utf-8") as file:
            file.write(json.dumps(event, ensure_ascii=False) + "\n")

    def _save(self) -> None:
        if not self._persist_path:
            return
        data = {
            "next_id": self._next_id,
            "tasks": {str(key): value for key, value in self._tasks.items()},
        }
        temporary = self._persist_path + ".tmp"
        with open(temporary, "w", encoding="utf-8") as file:
            json.dump(data, file, indent=2, ensure_ascii=False)
        os.replace(temporary, self._persist_path)

    def _load(self) -> None:
        assert self._persist_path is not None
        with open(self._persist_path, "r", encoding="utf-8") as file:
            data = json.load(file)
        self._next_id = data.get("next_id", 1)
        self._tasks = {
            int(key): value for key, value in data.get("tasks", {}).items()
        }
