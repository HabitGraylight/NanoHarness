"""NanoClaudeCode exports for the reusable Worktree extension."""

from nanoharness.extensions.worktrees import (
    TaskBoardBindingProtocol,
    WorktreeExtension,
    WorktreeExtensionConfig,
    WorktreeRegistry,
    register_worktree_tools,
)
from nanoharness.extensions.worktrees.registry import (
    _EVENTS_FILE,
    _INDEX_FILE,
    _WT_DIR,
)

__all__ = [
    "TaskBoardBindingProtocol",
    "WorktreeExtension",
    "WorktreeExtensionConfig",
    "WorktreeRegistry",
    "register_worktree_tools",
]
