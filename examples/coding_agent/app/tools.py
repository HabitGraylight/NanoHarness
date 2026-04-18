"""Coding agent tool assembly.

Combines script-based tools from configs/scripts/ with Python-native
tools for code search and file operations that are better handled
in-process than via shell environment variables.
"""

import subprocess
from pathlib import Path

from nanoharness.components.tools.dict_registry import DictToolRegistry
from nanoharness.components.tools.script_tools import ScriptToolRegistry


def build_tools(scripts_dir: str = "configs/scripts") -> ScriptToolRegistry:
    """Build the coding agent tool registry.

    Returns a merged registry containing:
    - All shell scripts from scripts_dir (git, file, sys ops)
    - Python-native search tool (better than shell for large codebases)
    """
    tools = ScriptToolRegistry(scripts_dir)

    py_tools = _build_python_tools()
    tools.merge(py_tools)

    return tools


def _build_python_tools() -> DictToolRegistry:
    """Register Python-native tools that supplement shell scripts."""
    registry = DictToolRegistry()

    @registry.tool
    def search_code(pattern: str, path: str = ".", file_glob: str = "*.py") -> str:
        """Search for a regex pattern in source files (like grep).

        Use this to find where functions, classes, or variables are defined
        or referenced across the codebase.
        """
        try:
            result = subprocess.run(
                ["grep", "-rn", "-E", "--include", file_glob, pattern, path],
                capture_output=True,
                text=True,
                timeout=30,
            )
            output = result.stdout.strip()
            if not output:
                return "No matches found."
            lines = output.splitlines()
            if len(lines) > 50:
                return "\n".join(lines[:50]) + f"\n... and {len(lines) - 50} more matches"
            return output
        except subprocess.TimeoutExpired:
            return "Search timed out after 30s."

    @registry.tool
    def list_files(pattern: str = "**/*.py", path: str = ".") -> str:
        """List files matching a glob pattern.

        Use this to understand the project structure before making changes.
        """
        files = sorted(str(p) for p in Path(path).glob(pattern) if p.is_file())
        if not files:
            return "No files found."
        if len(files) > 80:
            return "\n".join(files[:80]) + f"\n... and {len(files) - 80} more files"
        return "\n".join(files)

    return registry
