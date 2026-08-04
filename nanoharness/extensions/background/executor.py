"""Managed background shell command execution and notifications."""

import os
import queue
import signal
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence


_DEFAULT_TIMEOUT = 120
_MAX_PREVIEW_LINES = 20
_MAX_CONCURRENT = 4


@dataclass
class BackgroundTask:
    id: int
    command: str
    cwd: str
    timeout: int
    status: str = "running"
    exit_code: Optional[int] = None
    stdout: str = ""
    stderr: str = ""
    started_at: float = field(default_factory=time.time)
    finished_at: Optional[float] = None
    log_path: Optional[str] = None
    cancel_requested: bool = False


class BackgroundExecutor:
    """Own background processes, worker threads, and completion notices."""

    def __init__(
        self,
        workspace_root: str,
        scratch_dir: Optional[str] = None,
        max_concurrent: int = _MAX_CONCURRENT,
        *,
        shell_command: Optional[Sequence[str]] = None,
        restrict_cwd: bool = True,
        max_preview_lines: int = _MAX_PREVIEW_LINES,
        shutdown_timeout: float = 5.0,
    ):
        self._workspace_root = str(Path(workspace_root).resolve())
        self._scratch_dir = (
            str(Path(scratch_dir).resolve()) if scratch_dir else None
        )
        self._max_concurrent = max_concurrent
        self._shell_command = list(shell_command or ["bash", "-c"])
        self._restrict_cwd = restrict_cwd
        self._max_preview_lines = max_preview_lines
        self._shutdown_timeout = shutdown_timeout
        self._tasks: Dict[int, BackgroundTask] = {}
        self._threads: Dict[int, threading.Thread] = {}
        self._processes: Dict[int, subprocess.Popen] = {}
        self._next_id = 1
        self._lock = threading.RLock()
        self._notifications: queue.Queue = queue.Queue()
        self._closed = False

        if self._scratch_dir:
            os.makedirs(self._scratch_dir, exist_ok=True)

    @property
    def closed(self) -> bool:
        with self._lock:
            return self._closed

    def run(
        self,
        command: str,
        cwd: Optional[str] = None,
        timeout: int = _DEFAULT_TIMEOUT,
    ) -> int:
        """Start a command and return its task id immediately."""
        resolved_cwd = self._resolve_cwd(cwd)
        with self._lock:
            if self._closed:
                raise RuntimeError("BackgroundExecutor is closed")
            running = sum(
                1 for task in self._tasks.values() if task.status == "running"
            )
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
                cwd=resolved_cwd,
                timeout=timeout,
            )
            thread = threading.Thread(
                target=self._execute,
                args=(task,),
                name=f"nanoharness-background-{task_id}",
                daemon=True,
            )
            self._tasks[task_id] = task
            self._threads[task_id] = thread
            thread.start()
        return task_id

    def poll(self, task_id: int) -> Optional[Dict[str, Any]]:
        with self._lock:
            task = self._tasks.get(task_id)
            return _task_summary(task) if task is not None else None

    def notification(self, task_id: int) -> Optional[Dict[str, Any]]:
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return None
            return _task_notification(
                task,
                self._scratch_dir,
                max_preview_lines=self._max_preview_lines,
            )

    def drain(self) -> List[Dict[str, Any]]:
        results = []
        while True:
            try:
                task_id = self._notifications.get_nowait()
            except queue.Empty:
                break
            notification = self.notification(task_id)
            if notification is not None:
                results.append(notification)
        return results

    def list_running(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [
                _task_summary(task)
                for task in self._tasks.values()
                if task.status == "running"
            ]

    def close(self) -> None:
        """Cancel running commands and wait for their worker threads."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            running = [
                task for task in self._tasks.values() if task.status == "running"
            ]
            for task in running:
                task.cancel_requested = True
            processes = list(self._processes.values())
            threads = list(self._threads.values())

        for process in processes:
            _terminate_process(process)

        deadline = time.monotonic() + self._shutdown_timeout
        for thread in threads:
            remaining = max(0.0, deadline - time.monotonic())
            thread.join(timeout=remaining)

        with self._lock:
            remaining_processes = list(self._processes.values())
        for process in remaining_processes:
            _kill_process(process)
        for thread in threads:
            if thread.is_alive():
                thread.join(timeout=1.0)

        alive = [thread.name for thread in threads if thread.is_alive()]
        if alive:
            raise RuntimeError(f"Background workers did not stop: {alive}")

    def _resolve_cwd(self, cwd: Optional[str]) -> str:
        target = Path(cwd) if cwd else Path(self._workspace_root)
        if not target.is_absolute():
            target = Path(self._workspace_root) / target
        resolved = str(target.resolve())
        if self._restrict_cwd:
            try:
                common = os.path.commonpath([self._workspace_root, resolved])
            except ValueError as error:
                raise PermissionError(f"Working directory escapes workspace: {cwd}") from error
            if common != self._workspace_root:
                raise PermissionError(f"Working directory escapes workspace: {cwd}")
        return resolved

    def _execute(self, task: BackgroundTask) -> None:
        process: Optional[subprocess.Popen] = None
        try:
            process = subprocess.Popen(
                [*self._shell_command, task.command],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                cwd=task.cwd,
                start_new_session=os.name != "nt",
            )
            with self._lock:
                self._processes[task.id] = process
                cancel_requested = task.cancel_requested or self._closed
            if cancel_requested:
                _terminate_process(process)

            try:
                stdout, stderr = process.communicate(timeout=task.timeout)
            except subprocess.TimeoutExpired:
                _terminate_process(process)
                try:
                    stdout, stderr = process.communicate(timeout=1.0)
                except subprocess.TimeoutExpired:
                    _kill_process(process)
                    stdout, stderr = process.communicate()
                task.status = "cancelled" if task.cancel_requested else "timeout"
                task.stderr = stderr or (
                    "Cancelled during shutdown"
                    if task.cancel_requested
                    else f"Timeout after {task.timeout}s"
                )
            else:
                task.exit_code = process.returncode
                task.stdout = stdout or ""
                task.stderr = stderr or ""
                if task.cancel_requested:
                    task.status = "cancelled"
                else:
                    task.status = "completed" if process.returncode == 0 else "failed"
        except Exception as error:
            task.status = "cancelled" if task.cancel_requested else "failed"
            task.stderr = str(error)
        finally:
            if process is not None and task.exit_code is None:
                task.exit_code = process.returncode
            task.finished_at = time.time()
            self._write_log(task)
            with self._lock:
                self._processes.pop(task.id, None)
                self._threads.pop(task.id, None)
            self._notifications.put(task.id)

    def _write_log(self, task: BackgroundTask) -> None:
        if not self._scratch_dir:
            return
        log_path = os.path.join(self._scratch_dir, f"bg_{task.id}.log")
        try:
            with open(log_path, "w", encoding="utf-8") as file:
                file.write(f"$ {task.command}\n")
                file.write(f"exit: {task.exit_code}\n\n")
                if task.stdout:
                    file.write(task.stdout)
                if task.stderr:
                    file.write(f"\n--- stderr ---\n{task.stderr}")
            task.log_path = log_path
        except OSError:
            pass


def _terminate_process(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    try:
        if os.name != "nt":
            os.killpg(os.getpgid(process.pid), signal.SIGTERM)
        else:
            process.terminate()
    except (OSError, ProcessLookupError):
        pass


def _kill_process(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    try:
        if os.name != "nt":
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        else:
            process.kill()
    except (OSError, ProcessLookupError):
        pass


def _task_summary(task: BackgroundTask) -> Dict[str, Any]:
    return {
        "id": task.id,
        "command": task.command,
        "status": task.status,
        "exit_code": task.exit_code,
        "started_at": task.started_at,
        "finished_at": task.finished_at,
    }


def _task_notification(
    task: BackgroundTask,
    scratch_dir: Optional[str],
    *,
    max_preview_lines: int = _MAX_PREVIEW_LINES,
) -> Dict[str, Any]:
    preview_lines = (task.stdout or "").splitlines()
    truncated = len(preview_lines) > max_preview_lines
    preview = "\n".join(preview_lines[-max_preview_lines:])
    elapsed = ""
    if task.finished_at and task.started_at:
        elapsed = f" ({task.finished_at - task.started_at:.1f}s)"
    label = "Done" if task.status == "completed" else task.status.upper()
    lines = [
        f"[Background #{task.id} {label}] {task.command} — "
        f"exit: {task.exit_code}{elapsed}"
    ]
    if task.stderr and task.status != "completed":
        lines.append(f"stderr: {task.stderr[:200]}")
    if preview:
        lines.append(
            f"Output (last {max_preview_lines} lines):" if truncated else "Output:"
        )
        lines.append(preview)
    if task.log_path:
        lines.append(f"[Full output: {task.log_path}]")
    return {
        "task_id": task.id,
        "status": task.status,
        "message": "\n".join(lines),
    }
