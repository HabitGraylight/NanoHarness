"""Background task executor — run slow commands without blocking the agent loop.

Architecture:
    background_run("pytest")  → 立刻返回 task_id
    后台线程执行命令          → 完成后写入 notification queue
    下一轮 get_full_context() → drain() 注入完成通知到 messages

Usage:
    bg = BackgroundExecutor(workspace_root="/project")
    task_id = bg.run("pytest tests/ -v", timeout=120)
    # ... agent continues other work ...
    notifications = bg.drain()  # returns completed task results
"""

import os
import queue
import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from app.dispatch import DispatchRegistry, inprocess_handler, tool_result


# ── Defaults ──

_DEFAULT_TIMEOUT = 120       # seconds
_MAX_PREVIEW_LINES = 20      # lines kept in notification preview
_MAX_CONCURRENT = 4          # max running background tasks


# ── BackgroundTask ──


@dataclass
class BackgroundTask:
    """A single background command execution."""
    id: int
    command: str
    cwd: str
    timeout: int
    status: str = "running"       # running | completed | failed | timeout
    exit_code: Optional[int] = None
    stdout: str = ""
    stderr: str = ""
    started_at: float = field(default_factory=time.time)
    finished_at: Optional[float] = None
    log_path: Optional[str] = None


# ── BackgroundExecutor ──


class BackgroundExecutor:
    """Manage background command execution with notification draining.

    Thread-safe: run() starts daemon threads, drain() pulls from a queue.
    """

    def __init__(
        self,
        workspace_root: str,
        scratch_dir: Optional[str] = None,
        max_concurrent: int = _MAX_CONCURRENT,
    ):
        self._workspace_root = workspace_root
        self._scratch_dir = scratch_dir
        self._max_concurrent = max_concurrent
        self._tasks: Dict[int, BackgroundTask] = {}
        self._next_id = 1
        self._lock = threading.Lock()
        self._notifications: queue.Queue = queue.Queue()

        if scratch_dir:
            os.makedirs(scratch_dir, exist_ok=True)

    def run(
        self,
        command: str,
        cwd: Optional[str] = None,
        timeout: int = _DEFAULT_TIMEOUT,
    ) -> int:
        """Start a command in background. Returns task_id immediately."""
        with self._lock:
            running = sum(1 for t in self._tasks.values() if t.status == "running")
            if running >= self._max_concurrent:
                raise RuntimeError(
                    f"Too many background tasks ({running}/{self._max_concurrent}). "
                    "Wait for some to complete."
                )

            task_id = self._next_id
            self._next_id += 1

        task = BackgroundTask(
            id=task_id,
            command=command,
            cwd=cwd or self._workspace_root,
            timeout=timeout,
        )

        with self._lock:
            self._tasks[task_id] = task

        thread = threading.Thread(
            target=self._execute,
            args=(task,),
            daemon=True,
        )
        thread.start()
        return task_id

    def poll(self, task_id: int) -> Optional[Dict[str, Any]]:
        """Check status of a specific task. Returns None if not found."""
        task = self._tasks.get(task_id)
        if task is None:
            return None
        return _task_summary(task)

    def drain(self) -> List[Dict[str, Any]]:
        """Pull all newly completed task notifications (non-blocking)."""
        results = []
        while True:
            try:
                task_id = self._notifications.get_nowait()
            except queue.Empty:
                break
            task = self._tasks.get(task_id)
            if task:
                results.append(_task_notification(task, self._scratch_dir))
        return results

    def list_running(self) -> List[Dict[str, Any]]:
        """List all currently running tasks."""
        return [
            _task_summary(t)
            for t in self._tasks.values()
            if t.status == "running"
        ]

    # ── Internal ──

    def _execute(self, task: BackgroundTask):
        """Run command in subprocess, update task when done."""
        try:
            proc = subprocess.run(
                ["bash", "-c", task.command],
                capture_output=True,
                text=True,
                timeout=task.timeout,
                cwd=task.cwd,
            )
            task.exit_code = proc.returncode
            task.stdout = proc.stdout or ""
            task.stderr = proc.stderr or ""
            task.status = "completed" if proc.returncode == 0 else "failed"

        except subprocess.TimeoutExpired:
            task.status = "timeout"
            task.stdout = ""
            task.stderr = f"Timeout after {task.timeout}s"

        except Exception as e:
            task.status = "failed"
            task.stdout = ""
            task.stderr = str(e)

        finally:
            task.finished_at = time.time()

            # Save full output to disk if scratch_dir available
            if self._scratch_dir:
                log_path = os.path.join(self._scratch_dir, f"bg_{task.id}.log")
                try:
                    with open(log_path, "w", encoding="utf-8") as f:
                        f.write(f"$ {task.command}\n")
                        f.write(f"exit: {task.exit_code}\n\n")
                        if task.stdout:
                            f.write(task.stdout)
                        if task.stderr:
                            f.write(f"\n--- stderr ---\n{task.stderr}")
                    task.log_path = log_path
                except OSError:
                    pass

            self._notifications.put(task.id)


# ── Formatting helpers ──


def _task_summary(task: BackgroundTask) -> Dict[str, Any]:
    """Compact task info for poll responses."""
    return {
        "id": task.id,
        "command": task.command,
        "status": task.status,
        "exit_code": task.exit_code,
        "started_at": task.started_at,
        "finished_at": task.finished_at,
    }


def _task_notification(task: BackgroundTask, scratch_dir: Optional[str]) -> Dict[str, Any]:
    """Full notification for drain — includes truncated output."""
    preview_lines = (task.stdout or "").splitlines()
    truncated = len(preview_lines) > _MAX_PREVIEW_LINES
    preview = "\n".join(preview_lines[-_MAX_PREVIEW_LINES:])

    elapsed = ""
    if task.finished_at and task.started_at:
        elapsed = f" ({task.finished_at - task.started_at:.1f}s)"

    label = "Done" if task.status == "completed" else task.status.upper()
    lines = [f"[Background #{task.id} {label}] {task.command} — exit: {task.exit_code}{elapsed}"]

    if task.stderr and task.status != "completed":
        lines.append(f"stderr: {task.stderr[:200]}")

    if preview:
        if truncated:
            lines.append(f"Output (last {_MAX_PREVIEW_LINES} lines):")
        else:
            lines.append("Output:")
        lines.append(preview)

    if task.log_path:
        lines.append(f"[Full output: {task.log_path}]")

    return {
        "task_id": task.id,
        "status": task.status,
        "message": "\n".join(lines),
    }


# ── Tool registration ──


def register_background_tools(registry: DispatchRegistry, bg_executor: BackgroundExecutor):
    """Register background_run and background_poll tools."""

    def background_run(args: Dict) -> tool_result:
        command = args.get("command", "")
        if not command:
            return tool_result(ok=False, output="", error="command is required")

        timeout = int(args.get("timeout", _DEFAULT_TIMEOUT))
        cwd = args.get("cwd")

        try:
            task_id = bg_executor.run(command, cwd=cwd, timeout=timeout)
            return tool_result(
                ok=True,
                output=f"Started background task #{task_id}: {command}\n"
                       f"Use background_poll(id={task_id}) to check status.",
            )
        except RuntimeError as e:
            return tool_result(ok=False, output="", error=str(e))

    def background_poll(args: Dict) -> tool_result:
        task_id = args.get("id")
        if task_id is None:
            return tool_result(ok=False, output="", error="id is required")

        result = bg_executor.poll(int(task_id))
        if result is None:
            return tool_result(ok=False, output="", error=f"Task {task_id} not found")

        if result["status"] == "running":
            return tool_result(
                ok=True,
                output=f"Task #{task_id} still running: {result['command']}",
            )

        # Completed — show notification
        task = bg_executor._tasks[int(task_id)]
        notif = _task_notification(task, bg_executor._scratch_dir)
        return tool_result(ok=True, output=notif["message"])

    registry.register(
        name="background_run",
        handler=background_run,
        schema={
            "type": "function",
            "function": {
                "name": "background_run",
                "description": (
                    "Run a shell command in the background without blocking the agent loop. "
                    "Returns immediately with a task ID. The agent can continue other work. "
                    "Completion notifications are automatically injected before the next LLM call. "
                    "Use for slow commands: builds, test suites, installs."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "command": {
                            "type": "string",
                            "description": "Shell command to execute (e.g. 'pytest tests/ -v', 'npm install')",
                        },
                        "timeout": {
                            "type": "integer",
                            "description": "Max execution time in seconds (default 120)",
                        },
                        "cwd": {
                            "type": "string",
                            "description": "Working directory (default: workspace root)",
                        },
                    },
                    "required": ["command"],
                },
            },
        },
    )

    registry.register(
        name="background_poll",
        handler=background_poll,
        schema={
            "type": "function",
            "function": {
                "name": "background_poll",
                "description": "Check the status of a background task.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "id": {
                            "type": "integer",
                            "description": "Background task ID to check",
                        },
                    },
                    "required": ["id"],
                },
            },
        },
    )
