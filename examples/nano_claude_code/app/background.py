"""NanoClaudeCode exports for the reusable Background extension."""

from nanoharness.extensions.background import (
    BackgroundExecutor,
    BackgroundExtension,
    BackgroundExtensionConfig,
    BackgroundTask,
    register_background_tools,
)
from nanoharness.extensions.background.executor import (
    _DEFAULT_TIMEOUT,
    _MAX_CONCURRENT,
    _MAX_PREVIEW_LINES,
    _task_notification,
    _task_summary,
)

__all__ = [
    "BackgroundExecutor",
    "BackgroundExtension",
    "BackgroundExtensionConfig",
    "BackgroundTask",
    "register_background_tools",
]
