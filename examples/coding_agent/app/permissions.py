"""Coding agent permission policy.

Applies a stricter default than the general-purpose agent:
- Destructive git operations are denied outright.
- Write/commit/push operations require explicit user approval.
- Read-only operations are allowed freely.
"""

from nanoharness.components.permissions.rule_permission import RulePermissionManager
from nanoharness.core.schema import PermissionLevel


def build_permissions() -> RulePermissionManager:
    """Build the coding agent permission policy."""
    perms = RulePermissionManager(
        default_level=PermissionLevel.ALLOW,
    )

    # Deny outright — too dangerous for automated use
    perms.deny("git_reset")
    perms.deny("git_revert")

    # Confirm — user must approve before execution
    perms.confirm("git_push")
    perms.confirm("git_commit")
    perms.confirm("file_write")       # overwriting files needs approval
    perms.confirm("shell_exec")       # arbitrary commands need approval

    # Everything else (file_read, file_edit, file_list, git_status, etc.)
    # defaults to ALLOW

    return perms
