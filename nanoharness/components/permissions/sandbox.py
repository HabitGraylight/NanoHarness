import os
import subprocess
from typing import Any, Callable, Dict, List, Optional


class SandboxExecutor:
    """Wraps tool functions with execution restrictions.

    Provides a policy layer for:
    - Allowed file paths (restrict file read/write to specific directories)
    - Command timeout
    - Network toggle (policy flag, enforcement depends on OS level)

    Usage:
        sandbox = SandboxExecutor(allowed_paths=["/tmp/agent"], timeout=30)
        wrapped = sandbox.wrap(original_func)
        result = wrapped(**args)
    """

    def __init__(
        self,
        allowed_paths: Optional[List[str]] = None,
        timeout: Optional[int] = None,
        network_allowed: bool = True,
    ):
        self.allowed_paths = [os.path.abspath(p) for p in (allowed_paths or [])]
        self.timeout = timeout
        self.network_allowed = network_allowed

    def wrap(self, func: Callable) -> Callable:
        """Return a sandboxed version of func that enforces path restrictions."""

        def _wrapped(*args, **kwargs):
            self._check_args(kwargs)
            if self.timeout is not None:
                return self._run_with_timeout(func, args, kwargs)
            return func(*args, **kwargs)

        _wrapped.__name__ = func.__name__
        _wrapped.__doc__ = func.__doc__
        return _wrapped

    def _check_args(self, kwargs: Dict[str, Any]):
        """Check if any path-like arguments fall outside allowed_paths."""
        if not self.allowed_paths:
            return
        for key, value in kwargs.items():
            if isinstance(value, str) and self._looks_like_path(value):
                abs_path = os.path.abspath(value)
                if not any(abs_path.startswith(p) for p in self.allowed_paths):
                    raise PermissionError(
                        f"Sandbox: path '{value}' is outside allowed directories"
                    )

    @staticmethod
    def _looks_like_path(value: str) -> bool:
        return "/" in value or "\\" in value or value.startswith(".")

    def _run_with_timeout(self, func, args, kwargs):
        import threading

        result = [None]
        exception = [None]

        def target():
            try:
                result[0] = func(*args, **kwargs)
            except Exception as e:
                exception[0] = e

        thread = threading.Thread(target=target)
        thread.start()
        thread.join(timeout=self.timeout)
        if thread.is_alive():
            raise TimeoutError(f"Sandbox: tool execution exceeded {self.timeout}s")
        if exception[0]:
            raise exception[0]
        return result[0]

    def run_command(self, cmd: List[str], **kwargs) -> subprocess.CompletedProcess:
        """Execute a subprocess command with sandbox restrictions."""
        if not self.network_allowed and self._is_network_command(cmd):
            raise PermissionError("Sandbox: network access is disabled")
        kwargs.setdefault("timeout", self.timeout)
        return subprocess.run(cmd, capture_output=True, text=True, **kwargs)

    @staticmethod
    def _is_network_command(cmd: List[str]) -> bool:
        network_tools = {"curl", "wget", "ssh", "scp", "rsync", "nc"}
        return os.path.basename(cmd[0]) in network_tools if cmd else False
