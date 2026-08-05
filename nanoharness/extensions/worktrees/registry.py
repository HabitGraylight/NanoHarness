"""Git worktree registry with task binding and audit events."""

import json
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Protocol, Sequence, Tuple


_WT_DIR = ".worktrees"
_INDEX_FILE = "index.json"
_EVENTS_FILE = "events.jsonl"
_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class TaskBoardBindingProtocol(Protocol):
    def get(self, task_id: int) -> Optional[Dict[str, Any]]:
        ...

    def bind_worktree(self, task_id: int, name: str) -> None:
        ...

    def unbind_worktree(
        self,
        task_id: int,
        action: str,
        closeout_record: dict,
    ) -> None:
        ...

    def complete(self, task_id: int) -> Dict[str, Any]:
        ...


class WorktreeRegistry:
    """Persist and operate isolated Git execution lanes."""

    def __init__(
        self,
        workspace_root: str,
        task_board: Optional[TaskBoardBindingProtocol] = None,
        index_path: Optional[str] = None,
        *,
        worktree_dir: str = _WT_DIR,
        branch_prefix: str = "wt/",
        git_executable: str = "git",
        shell_command: Optional[Sequence[str]] = None,
        command_timeout: int = 30,
    ):
        self._root = str(Path(workspace_root).resolve())
        directory = Path(worktree_dir)
        if not directory.is_absolute():
            directory = Path(self._root) / directory
        self._wt_dir = str(directory.resolve())
        if os.path.commonpath([self._root, self._wt_dir]) != self._root:
            raise ValueError("worktree_dir must stay inside workspace_root")
        raw_index = Path(index_path) if index_path else Path(self._wt_dir) / _INDEX_FILE
        if not raw_index.is_absolute():
            raw_index = Path(self._root) / raw_index
        self._index_path = str(raw_index.resolve())
        if os.path.commonpath([self._root, self._index_path]) != self._root:
            raise ValueError("index_path must stay inside workspace_root")
        self._events_path = str(Path(self._index_path).parent / _EVENTS_FILE)
        self._task_board = task_board
        self._branch_prefix = branch_prefix
        self._git_executable = git_executable
        self._shell_command = list(shell_command or ["bash", "-c"])
        self._command_timeout = command_timeout
        self._index: Dict[str, Dict[str, Any]] = {}
        os.makedirs(self._wt_dir, exist_ok=True)
        os.makedirs(str(Path(self._index_path).parent), exist_ok=True)
        if os.path.exists(self._index_path):
            self._load_index()

    def create(self, name: str, task_id: int) -> Dict[str, Any]:
        self._validate_name(name)
        if name in self._index:
            raise ValueError(f"Worktree '{name}' already exists")
        if self._task_board is not None and self._task_board.get(task_id) is None:
            raise KeyError(f"Task {task_id} not found")

        absolute_path = str(Path(self._wt_dir) / name)
        relative_path = os.path.relpath(absolute_path, self._root)
        branch = f"{self._branch_prefix}{name}"
        result = subprocess.run(
            [
                self._git_executable,
                "worktree",
                "add",
                "-b",
                branch,
                absolute_path,
                "HEAD",
            ],
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
            "path": relative_path,
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
        if self._task_board is not None:
            self._task_board.bind_worktree(task_id, name)
        return record

    def enter(self, name: str) -> Dict[str, Any]:
        record = self._require(name)
        record["last_entered_at"] = time.time()
        self._save_index()
        self._emit_event(
            "worktree.enter",
            name=name,
            task_id=record.get("task_id"),
        )
        return record

    def run(
        self,
        name: str,
        command: str,
        timeout: Optional[int] = None,
    ) -> Tuple[int, str]:
        record = self._require(name)
        absolute_path = str(Path(self._root) / record["path"])
        result = subprocess.run(
            [*self._shell_command, command],
            capture_output=True,
            text=True,
            cwd=absolute_path,
            timeout=timeout or self._command_timeout,
        )
        output = (result.stdout or "") + (result.stderr or "")
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
        if action not in ("keep", "remove"):
            raise ValueError(
                f"action must be 'keep' or 'remove', got '{action}'"
            )
        record = self._require(name)
        closeout_record = {
            "action": action,
            "reason": reason,
            "at": time.time(),
        }
        if action == "remove":
            absolute_path = str(Path(self._root) / record["path"])
            result = subprocess.run(
                [self._git_executable, "worktree", "remove", absolute_path],
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
        record["closeout"] = closeout_record
        self._save_index()
        self._emit_event(
            f"worktree.closeout.{action}",
            name=name,
            task_id=record.get("task_id"),
            reason=reason,
        )
        task_id = record.get("task_id")
        if self._task_board is not None and task_id:
            if complete_task:
                try:
                    self._task_board.complete(task_id)
                except (KeyError, ValueError):
                    pass
            try:
                self._task_board.unbind_worktree(
                    task_id,
                    action,
                    closeout_record,
                )
            except (KeyError, ValueError):
                pass
        return record

    def list(self, status: Optional[str] = None) -> List[Dict[str, Any]]:
        records = list(self._index.values())
        if status:
            records = [record for record in records if record["status"] == status]
        return records

    def get(self, name: str) -> Optional[Dict[str, Any]]:
        return self._index.get(name)

    def _validate_name(self, name: str) -> None:
        if not _SAFE_NAME.fullmatch(name) or name in (".", ".."):
            raise ValueError(
                "Worktree name must use letters, numbers, '.', '_' or '-' "
                "and cannot contain path separators"
            )

    def _require(self, name: str) -> Dict[str, Any]:
        record = self._index.get(name)
        if record is None:
            raise KeyError(f"Worktree '{name}' not found")
        return record

    def _save_index(self) -> None:
        data = {"worktrees": list(self._index.values())}
        temporary = self._index_path + ".tmp"
        with open(temporary, "w", encoding="utf-8") as file:
            json.dump(data, file, indent=2, ensure_ascii=False)
        os.replace(temporary, self._index_path)

    def _load_index(self) -> None:
        with open(self._index_path, "r", encoding="utf-8") as file:
            data = json.load(file)
        self._index = {
            record["name"]: record
            for record in data.get("worktrees", [])
        }

    def _emit_event(self, event: str, **kwargs: Any) -> None:
        record = {"event": event, "ts": time.time(), **kwargs}
        with open(self._events_path, "a", encoding="utf-8") as file:
            file.write(json.dumps(record, ensure_ascii=False) + "\n")
