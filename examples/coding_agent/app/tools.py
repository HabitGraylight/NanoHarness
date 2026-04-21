"""Coding agent tool assembly.

Builds a DispatchRegistry with path sandboxing and bash-wrapped execution.
Adding a tool = adding a handler + adding a schema.
"""

import os

from app.dispatch import DispatchRegistry
from app.handlers import register_script_tools, register_python_tools


def build_tools(
    scripts_dir: str = "configs/scripts",
    workspace_root: str | None = None,
) -> DispatchRegistry:
    """Build the coding agent tool registry.

    Args:
        scripts_dir: Path to .sh script directory.
        workspace_root: Root directory for path sandboxing.
            Defaults to the grandparent of this file (the coding_agent/ dir).

    Returns:
        DispatchRegistry implementing BaseToolRegistry.
    """
    if workspace_root is None:
        workspace_root = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..")
        )

    registry = DispatchRegistry(workspace_root=workspace_root)
    register_script_tools(registry, scripts_dir)
    register_python_tools(registry)
    return registry
