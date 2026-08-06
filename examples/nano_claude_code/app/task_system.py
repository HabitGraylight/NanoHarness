"""NanoClaudeCode exports for the reusable Task Board extension."""

from nanoharness.extensions.tasks import (
    TaskBoard,
    TaskExtension,
    TaskExtensionConfig,
    TaskStatus,
    is_claimable,
    is_ready,
    make_task,
    register_task_tools,
)
from nanoharness.extensions.tasks.board import _new_id, _task_allows_role

__all__ = [
    "TaskBoard",
    "TaskExtension",
    "TaskExtensionConfig",
    "TaskStatus",
    "is_claimable",
    "is_ready",
    "make_task",
    "register_task_tools",
]
