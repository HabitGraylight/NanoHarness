"""Dispatch layer for coding agent tools.

Extends the kernel's DictToolRegistry with:
- Path sandboxing (workspace root confinement)
- ToolResult unwrapping (handlers return tool_result, call() returns str)
- Handler factories (bash_handler, bash_wrap, inprocess_handler)

Adding a tool = adding a handler + adding a schema.
"""

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from nanoharness.components.tools.dict_registry import DictToolRegistry


# ── App-layer result type ──


@dataclass
class tool_result:
    """Unified return type from every tool handler."""
    ok: bool
    output: str
    error: Optional[str] = None


# ── Registry ──


class DispatchRegistry(DictToolRegistry):
    """Tool registry with path sandboxing and tool_result unwrapping.

    Inherits from DictToolRegistry (kernel):
    - _tools storage (name → {"func": handler, "schema": schema})
    - get_tool_schemas()
    - merge()
    - reset()

    Adds:
    - sandbox_path() / _sandbox_args() for workspace confinement
    - call() override: sandbox paths → unwrap tool_result → return str
    """

    def __init__(self, workspace_root: str, timeout: int = 60):
        super().__init__()
        self._workspace_root = str(Path(workspace_root).resolve())
        self._timeout = timeout
        self._path_params: Dict[str, List[str]] = {}

    @property
    def workspace_root(self) -> str:
        return self._workspace_root

    @property
    def dispatch_map(self) -> Dict[str, Callable]:
        """Compat: map of tool_name → handler function."""
        return {name: info["func"] for name, info in self._tools.items()}

    @property
    def schemas(self) -> Dict[str, Dict]:
        """Compat: map of tool_name → JSON schema."""
        return {name: info["schema"] for name, info in self._tools.items()}

    def register(
        self,
        name: str,
        handler: Callable[[Dict], tool_result],
        schema: Dict,
        path_params: Optional[List[str]] = None,
    ):
        """Register a tool: handler + schema + optional path param names."""
        self._tools[name] = {
            "func": handler,
            "schema": schema,
        }
        self._path_params[name] = path_params or []

    def sandbox_path(self, path: str) -> str:
        """Resolve and validate a path against the workspace root."""
        target = Path(path)
        if not target.is_absolute():
            target = Path(self._workspace_root) / target
        resolved = str(target.resolve())
        if not resolved.startswith(self._workspace_root):
            raise PermissionError(f"Path escapes workspace: {path}")
        return resolved

    def call(self, name: str, args: Dict) -> Any:
        """Dispatch: sandbox paths → run handler → unwrap tool_result → return string."""
        if name not in self._tools:
            raise KeyError(
                f"Tool '{name}' not found. Available: {list(self._tools)}"
            )
        safe_args = self._sandbox_args(args, self._path_params.get(name, []))
        result = self._tools[name]["func"](safe_args)
        if isinstance(result, tool_result):
            if not result.ok:
                raise RuntimeError(result.error)
            return result.output
        return str(result)

    def _sandbox_args(self, args: Dict, path_keys: List[str]) -> Dict:
        """Validate declared path params against workspace root."""
        safe = dict(args)
        for key in path_keys:
            if key not in safe:
                continue
            safe[key] = self.sandbox_path(safe[key])
        return safe


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
                capture_output=True, text=True,
                timeout=timeout, env=env,
            )
            if proc.returncode != 0:
                return tool_result(
                    ok=False, output="",
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
                cmd, capture_output=True, text=True, timeout=timeout,
            )
            output = proc.stdout.strip()
            if proc.returncode != 0:
                if not output and proc.stderr.strip():
                    return tool_result(ok=False, output="", error=proc.stderr.strip())
                if not output:
                    return tool_result(ok=True, output="No matches found.")
            return tool_result(ok=True, output=output)
        except subprocess.TimeoutExpired:
            return tool_result(ok=False, output="", error=f"Timeout after {timeout}s")
    return handler


def inprocess_handler(func: Callable) -> Callable[[Dict], tool_result]:
    """Wrap an in-process Python function as a handler."""
    def handler(args: Dict) -> tool_result:
        try:
            result = func(**args)
            return tool_result(ok=True, output=str(result))
        except Exception as e:
            return tool_result(ok=False, output="", error=str(e))
    return handler
