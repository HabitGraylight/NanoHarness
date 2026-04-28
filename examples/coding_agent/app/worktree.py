"""Worktree task isolation — each task gets its own git worktree directory.

WorktreeRegistry manages .worktrees/index.json and provides operations
for creating, entering, running commands in, and closing out worktrees.

Core mental model:
    task record  → "what to do"
    worktree record → "where to do it"
    linked by task_id

Usage:
    wt = WorktreeRegistry(workspace_root=root, task_board=board)
    wt.create("auth-refactor", task_id=12)
    wt.enter("auth-refactor")
    wt.run("auth-refactor", "pytest tests/auth -q")
    wt.closeout("auth-refactor", action="keep", reason="Need follow-up review")
"""

import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.dispatch import DispatchRegistry, tool_result


# ── Defaults ──

_WT_DIR = ".worktrees"
_INDEX_FILE = "index.json"
_EVENTS_FILE = "events.jsonl"


# ── WorktreeRegistry ──


class WorktreeRegistry:
    """Manage git worktrees as isolated execution lanes for tasks.

    Each worktree is a separate checkout of the repo, bound to a task ID.
    All state is persisted to .worktrees/index.json.
    Events are appended to .worktrees/events.jsonl.
    """

    def __init__(
        self,
        workspace_root: str,
        task_board=None,
        index_path: Optional[str] = None,
    ):
        self._root = workspace_root
        self._wt_dir = os.path.join(workspace_root, _WT_DIR)
        self._index_path = index_path or os.path.join(self._wt_dir, _INDEX_FILE)
        self._events_path = os.path.join(self._wt_dir, _EVENTS_FILE)
        self._task_board = task_board
        self._index: Dict[str, Dict[str, Any]] = {}
        os.makedirs(self._wt_dir, exist_ok=True)
        if os.path.exists(self._index_path):
            self._load_index()

    # ── CRUD ──

    def create(self, name: str, task_id: int) -> Dict[str, Any]:
        """Create a new worktree bound to a task.

        Runs `git worktree add -b wt/{name} {path} HEAD`.
        Raises ValueError if name already exists.
        Raises RuntimeError if git command fails.
        """
        if name in self._index:
            raise ValueError(f"Worktree '{name}' already exists")

        rel_path = os.path.join(_WT_DIR, name)
        abs_path = os.path.join(self._root, rel_path)
        branch = f"wt/{name}"

        # Create git worktree
        result = subprocess.run(
            ["git", "worktree", "add", "-b", branch, abs_path, "HEAD"],
            capture_output=True,
            text=True,
            cwd=self._root,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"git worktree add failed: {result.stderr.strip()}"
            )

        record = {
            "name": name,
            "path": rel_path,
            "branch": branch,
            "task_id": task_id,
            "status": "active",
            "last_entered_at": None,
            "last_command_at": None,
            "last_command_preview": None,
            "closeout": None,
        }
        self._index[name] = record
        self._save_index()
        self._emit_event("worktree.create", name=name, task_id=task_id)

        # Bind worktree to task
        if self._task_board:
            try:
                self._task_board.bind_worktree(task_id, name)
            except (KeyError, ValueError):
                pass  # Task may not exist — don't fail worktree creation

        return record

    def enter(self, name: str) -> Dict[str, Any]:
        """Mark a worktree as entered (updates tracking fields).

        Raises KeyError if worktree not found.
        """
        record = self._require(name)
        record["last_entered_at"] = time.time()
        self._save_index()
        self._emit_event(
            "worktree.enter", name=name, task_id=record.get("task_id"),
        )
        return record

    def run(self, name: str, command: str, timeout: int = 30) -> Tuple[int, str]:
        """Run a command in the worktree directory.

        Returns (exit_code, output).
        Updates last_command_at and last_command_preview.
        """
        record = self._require(name)
        abs_path = os.path.join(self._root, record["path"])

        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            cwd=abs_path,
            timeout=timeout,
        )
        output = result.stdout + result.stderr

        record["last_command_at"] = time.time()
        preview = command[:80] + ("..." if len(command) > 80 else "")
        record["last_command_preview"] = preview
        self._save_index()
        self._emit_event(
            "worktree.run",
            name=name,
            task_id=record.get("task_id"),
            command=preview,
            exit_code=result.returncode,
        )

        return result.returncode, output

    def closeout(
        self,
        name: str,
        action: str,
        reason: str = "",
        complete_task: bool = False,
    ) -> Dict[str, Any]:
        """Close out a worktree with an explicit keep/remove decision.

        action: "keep" or "remove"
        reason: why this decision was made
        complete_task: if True, also complete the bound task
        """
        if action not in ("keep", "remove"):
            raise ValueError(f"action must be 'keep' or 'remove', got '{action}'")

        record = self._require(name)
        closeout_rec = {
            "action": action,
            "reason": reason,
            "at": time.time(),
        }

        if action == "remove":
            abs_path = os.path.join(self._root, record["path"])
            result = subprocess.run(
                ["git", "worktree", "remove", abs_path],
                capture_output=True,
                text=True,
                cwd=self._root,
            )
            if result.returncode != 0:
                raise RuntimeError(
                    f"git worktree remove failed: {result.stderr.strip()}"
                )
            record["status"] = "removed"
        else:
            record["status"] = "kept"

        record["closeout"] = closeout_rec
        self._save_index()
        self._emit_event(
            f"worktree.closeout.{action}",
            name=name,
            task_id=record.get("task_id"),
            reason=reason,
        )

        # Optionally complete the bound task
        if complete_task and self._task_board and record.get("task_id"):
            try:
                self._task_board.complete(record["task_id"])
            except (KeyError, ValueError):
                pass

        # Update task's worktree binding
        if self._task_board and record.get("task_id"):
            try:
                self._task_board.unbind_worktree(
                    record["task_id"], action, closeout_rec,
                )
            except (KeyError, ValueError):
                pass

        return record

    def list(self, status: Optional[str] = None) -> List[Dict[str, Any]]:
        """List worktrees, optionally filtered by status."""
        records = list(self._index.values())
        if status:
            records = [r for r in records if r["status"] == status]
        return records

    def get(self, name: str) -> Optional[Dict[str, Any]]:
        """Get a worktree record by name. Returns None if not found."""
        return self._index.get(name)

    # ── Internals ──

    def _require(self, name: str) -> Dict[str, Any]:
        record = self._index.get(name)
        if record is None:
            raise KeyError(f"Worktree '{name}' not found")
        return record

    def _save_index(self):
        data = {"worktrees": list(self._index.values())}
        tmp = self._index_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp, self._index_path)

    def _load_index(self):
        with open(self._index_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        self._index = {
            r["name"]: r for r in data.get("worktrees", [])
        }

    def _emit_event(self, event: str, **kwargs):
        """Append an event to the JSONL event log."""
        event_rec = {
            "event": event,
            "ts": time.time(),
            **kwargs,
        }
        with open(self._events_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(event_rec, ensure_ascii=False) + "\n")


# ── Tool registration ──


def register_worktree_tools(registry: DispatchRegistry, wt_registry: WorktreeRegistry):
    """Register worktree_create, worktree_enter, worktree_run, worktree_closeout, worktree_list."""

    def worktree_create(args: Dict) -> tool_result:
        name = args.get("name", "").strip()
        task_id = args.get("task_id")
        if not name:
            return tool_result(ok=False, output="", error="name is required")
        if task_id is None:
            return tool_result(ok=False, output="", error="task_id is required")
        try:
            record = wt_registry.create(name, task_id=int(task_id))
            return tool_result(
                ok=True,
                output=(
                    f"Created worktree '{record['name']}' "
                    f"(branch: {record['branch']}, path: {record['path']}) "
                    f"for task #{task_id}"
                ),
            )
        except (ValueError, RuntimeError) as e:
            return tool_result(ok=False, output="", error=str(e))

    def worktree_enter(args: Dict) -> tool_result:
        name = args.get("name", "").strip()
        if not name:
            return tool_result(ok=False, output="", error="name is required")
        try:
            record = wt_registry.enter(name)
            return tool_result(
                ok=True,
                output=f"Entered worktree '{name}' at {record['path']}",
            )
        except KeyError as e:
            return tool_result(ok=False, output="", error=str(e))

    def worktree_run(args: Dict) -> tool_result:
        name = args.get("name", "").strip()
        command = args.get("command", "").strip()
        if not name:
            return tool_result(ok=False, output="", error="name is required")
        if not command:
            return tool_result(ok=False, output="", error="command is required")
        try:
            exit_code, output = wt_registry.run(name, command)
            status = "OK" if exit_code == 0 else f"EXIT {exit_code}"
            return tool_result(
                ok=exit_code == 0,
                output=f"[{status}] {output}",
                error="" if exit_code == 0 else f"Command exited with code {exit_code}",
            )
        except (KeyError, subprocess.TimeoutExpired) as e:
            return tool_result(ok=False, output="", error=str(e))

    def worktree_closeout(args: Dict) -> tool_result:
        name = args.get("name", "").strip()
        action = args.get("action", "").strip()
        reason = args.get("reason", "").strip()
        complete_task = args.get("complete_task", False)
        if not name:
            return tool_result(ok=False, output="", error="name is required")
        if action not in ("keep", "remove"):
            return tool_result(
                ok=False, output="",
                error="action must be 'keep' or 'remove'",
            )
        try:
            record = wt_registry.closeout(
                name, action=action, reason=reason,
                complete_task=complete_task,
            )
            msg = f"Closeout '{name}': {action}"
            if reason:
                msg += f" — {reason}"
            return tool_result(ok=True, output=msg)
        except (KeyError, ValueError, RuntimeError) as e:
            return tool_result(ok=False, output="", error=str(e))

    def worktree_list(args: Dict) -> tool_result:
        status = args.get("status", "").strip() or None
        records = wt_registry.list(status=status)
        if not records:
            return tool_result(ok=True, output="No worktrees found.")
        lines = []
        for r in records:
            task_info = f" task=#{r['task_id']}" if r.get("task_id") else ""
            lines.append(
                f"  {r['name']} [{r['status']}] "
                f"branch={r['branch']}{task_info}"
            )
        return tool_result(ok=True, output="\n".join(lines))

    for t_name, handler, desc, params in [
        (
            "worktree_create",
            worktree_create,
            "Create a git worktree as an isolated execution lane for a task. "
            "Creates a new branch wt/{name} and checks out HEAD into a separate directory.",
            {
                "name": {"type": "string", "description": "Worktree name (used as directory and branch name)"},
                "task_id": {"type": "integer", "description": "Task ID to bind this worktree to"},
            },
        ),
        (
            "worktree_enter",
            worktree_enter,
            "Mark a worktree as entered. Updates tracking fields (last_entered_at).",
            {
                "name": {"type": "string", "description": "Worktree name to enter"},
            },
        ),
        (
            "worktree_run",
            worktree_run,
            "Run a shell command inside a worktree's directory. "
            "Commands execute in isolation — they only affect that worktree's files.",
            {
                "name": {"type": "string", "description": "Worktree name"},
                "command": {"type": "string", "description": "Shell command to run in the worktree directory"},
            },
        ),
        (
            "worktree_closeout",
            worktree_closeout,
            "Close out a worktree with an explicit keep or remove decision. "
            "Optionally complete the bound task simultaneously.",
            {
                "name": {"type": "string", "description": "Worktree name to close out"},
                "action": {
                    "type": "string",
                    "enum": ["keep", "remove"],
                    "description": "'keep' to preserve the directory, 'remove' to delete it",
                },
                "reason": {"type": "string", "description": "Why this decision was made"},
                "complete_task": {
                    "type": "boolean",
                    "description": "If true, also mark the bound task as completed",
                },
            },
        ),
        (
            "worktree_list",
            worktree_list,
            "List worktrees and their status. Optionally filter by status.",
            {
                "status": {
                    "type": "string",
                    "enum": ["active", "kept", "removed"],
                    "description": "Filter by status",
                },
            },
        ),
    ]:
        required = []
        if t_name == "worktree_create":
            required = ["name", "task_id"]
        elif t_name in ("worktree_enter", "worktree_run", "worktree_closeout"):
            required = ["name"]
        if t_name == "worktree_run":
            required.append("command")
        if t_name == "worktree_closeout":
            required.append("action")

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
