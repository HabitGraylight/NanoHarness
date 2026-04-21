"""Dispatch layer for coding agent tools.

Core abstractions:
- tool_result: unified return type from every handler
- DispatchRegistry: maps tool names to handlers + schemas, enforces path sandboxing
- bash_handler / bash_wrap / inprocess_handler: handler factory functions

Adding a tool = adding a handler + adding a schema.
"""

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from nanoharness.core.base import BaseToolRegistry


@dataclass
class tool_result:
    """Unified return type from every tool handler.

    ok=True  → output contains the result string
    ok=False → error contains the error description
    """
    ok: bool
    output: str
    error: Optional[str] = None


class DispatchRegistry(BaseToolRegistry):
    """Tool registry with explicit dispatch map and path sandboxing.

    dispatch_map: tool_name → handler function  (code dispatch)
    schemas:      tool_name → JSON schema       (model description)
    """

    def __init__(self, workspace_root: str, timeout: int = 60):
        self._workspace_root = str(Path(workspace_root).resolve())
        self._timeout = timeout
        self.dispatch_map: Dict[str, Callable[[Dict], tool_result]] = {}
        self.schemas: Dict[str, Dict] = {}
        self._path_params: Dict[str, List[str]] = {}

    @property
    def workspace_root(self) -> str:
        return self._workspace_root

    def register(
        self,
        name: str,
        handler: Callable[[Dict], tool_result],
        schema: Dict,
        path_params: Optional[List[str]] = None,
    ):
        """Register a tool: handler + schema + optional path param names."""
        self.dispatch_map[name] = handler
        self.schemas[name] = schema
        self._path_params[name] = path_params or []

    def sandbox_path(self, path: str) -> str:
        """Resolve and validate a path against the workspace root.

        Returns the absolute path if it stays within workspace.
        Raises PermissionError if the path escapes.
        """
        # Treat relative paths as relative to workspace root
        target = Path(path)
        if not target.is_absolute():
            target = Path(self._workspace_root) / target
        resolved = str(target.resolve())

        if not resolved.startswith(self._workspace_root):
            raise PermissionError(f"Path escapes workspace: {path}")
        return resolved

    def call(self, name: str, args: Dict) -> Any:
        """Dispatch: sandbox paths → run handler → return output string."""
        if name not in self.dispatch_map:
            raise KeyError(
                f"Tool '{name}' not found. Available: {list(self.dispatch_map)}"
            )

        # Sandbox path parameters before passing to handler
        safe_args = self._sandbox_args(args, self._path_params.get(name, []))

        result = self.dispatch_map[name](safe_args)
        if not result.ok:
            raise RuntimeError(result.error)
        return result.output

    def _sandbox_args(self, args: Dict, path_keys: List[str]) -> Dict:
        """Validate declared path params against workspace root."""
        safe = dict(args)
        for key in path_keys:
            if key not in safe:
                continue
            safe[key] = self.sandbox_path(safe[key])
        return safe

    def get_tool_schemas(self) -> List[Dict]:
        return list(self.schemas.values())

    def reset(self):
        self.dispatch_map.clear()
        self.schemas.clear()
        self._path_params.clear()


# ── Handler factories ──


def bash_handler(script_path: str, timeout: int = 60) -> Callable[[Dict], tool_result]:
    """Create a handler that runs a shell script via bash subprocess."""
    def handler(args: Dict) -> tool_result:
        env = os.environ.copy()
        for k, v in args.items():
            if isinstance(v, bool):
                env[k] = "true" if v else "false"
            else:
                env[k] = str(v)
        try:
            proc = subprocess.run(
                ["bash", str(script_path)],
                capture_output=True,
                text=True,
                timeout=timeout,
                env=env,
            )
            if proc.returncode != 0:
                return tool_result(
                    ok=False,
                    output="",
                    error=proc.stderr.strip() or f"Script failed (exit {proc.returncode})",
                )
            return tool_result(ok=True, output=proc.stdout.strip())
        except subprocess.TimeoutExpired:
            return tool_result(ok=False, output="", error=f"Timeout after {timeout}s")
    return handler


def bash_wrap(
    command_builder: Callable[[Dict], List[str]],
    timeout: int = 30,
) -> Callable[[Dict], tool_result]:
    """Wrap a command-builder function as a bash subprocess handler."""
    def handler(args: Dict) -> tool_result:
        cmd = command_builder(args)
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            output = proc.stdout.strip()
            if proc.returncode != 0:
                # grep returns 1 on no match — not a real error
                if not output and proc.stderr.strip():
                    return tool_result(ok=False, output="", error=proc.stderr.strip())
                if not output:
                    return tool_result(ok=True, output="No matches found.")
            return tool_result(ok=True, output=output)
        except subprocess.TimeoutExpired:
            return tool_result(ok=False, output="", error=f"Timeout after {timeout}s")
    return handler


def inprocess_handler(func: Callable) -> Callable[[Dict], tool_result]:
    """Wrap an in-process Python function as a handler (for memory tools etc.)."""
    def handler(args: Dict) -> tool_result:
        try:
            result = func(**args)
            return tool_result(ok=True, output=str(result))
        except Exception as e:
            return tool_result(ok=False, output="", error=str(e))
    return handler
