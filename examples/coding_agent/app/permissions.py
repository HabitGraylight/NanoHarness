"""Permission gate: 4-step pipeline for tool call authorization.

    tool_call
      │
      ▼
  1. deny rules     → hit → REJECT
      │
      ▼
  2. mode check     → yolo: pass everything / auto: deny unknowns
      │
      ▼
  3. allow rules    → hit → PASS
      │
      ▼
  4. ask user       → user confirms or rejects

Modes:
    interactive  (default) — unlisted tools ask user
    auto         — unlisted tools auto-deny (only allow_rules pass)
    yolo         — everything not denied passes (no user prompts)
"""

import fnmatch
from abc import ABC, abstractmethod
from enum import Enum
from typing import Callable, Dict, List, Optional


# ── App-layer types (engine duck-types these) ──


class PermissionLevel(str, Enum):
    DENY = "deny"
    CONFIRM = "confirm"
    ALLOW = "allow"


class BasePermissionManager(ABC):
    """App-layer ABC — engine duck-types enforce()."""

    @abstractmethod
    def check(self, tool_name: str, args: Dict) -> PermissionLevel: ...

    @abstractmethod
    def enforce(self, tool_name: str, args: Dict) -> Optional[str]: ...


class GateMode(str, Enum):
    AUTO = "auto"
    INTERACTIVE = "interactive"
    YOLO = "yolo"


class PermissionGate(BasePermissionManager):
    """4-step permission pipeline: deny → mode → allow → ask.

    Separates deny rules and allow rules into distinct lists,
    with a runtime mode that controls the fallback behavior.
    """

    def __init__(
        self,
        mode: GateMode = GateMode.INTERACTIVE,
        approval_callback: Optional[Callable[[str, Dict], bool]] = None,
    ):
        self._deny_rules: List[str] = []   # glob patterns
        self._allow_rules: List[str] = []  # glob patterns
        self._mode = mode
        self._approval_callback = approval_callback

    # ── Configuration ──

    def deny(self, pattern: str):
        """Add a deny rule (glob pattern: 'git_reset', 'git_*', '*')."""
        self._deny_rules.append(pattern)

    def allow(self, pattern: str):
        """Add an allow rule (glob pattern)."""
        self._allow_rules.append(pattern)

    def set_mode(self, mode: GateMode):
        """Switch runtime mode: auto / interactive / yolo."""
        self._mode = mode

    @property
    def mode(self) -> GateMode:
        return self._mode

    # ── 4-step pipeline ──

    def check(self, tool_name: str, args: Dict) -> PermissionLevel:
        """Run the 4-step pipeline. Returns the decided level."""

        # Step 1: deny rules — hit → REJECT
        if self._matches(tool_name, self._deny_rules):
            return PermissionLevel.DENY

        # Step 2: mode check — yolo bypasses everything else
        if self._mode == GateMode.YOLO:
            return PermissionLevel.ALLOW

        # Step 3: allow rules — hit → PASS
        if self._matches(tool_name, self._allow_rules):
            return PermissionLevel.ALLOW

        # Step 4: fallback — depends on mode
        if self._mode == GateMode.INTERACTIVE:
            return PermissionLevel.CONFIRM  # ask user

        # auto mode: unlisted → deny
        return PermissionLevel.DENY

    def enforce(self, tool_name: str, args: Dict) -> Optional[str]:
        """Check permissions and handle approval.

        Returns None if allowed, or an error message string if blocked.
        """
        level = self.check(tool_name, args)

        if level == PermissionLevel.DENY:
            return f"Permission denied for tool '{tool_name}'"

        if level == PermissionLevel.CONFIRM:
            if self._approval_callback:
                approved = self._approval_callback(tool_name, args)
            else:
                approved = _default_terminal_approve(tool_name, args)
            if not approved:
                return f"Tool '{tool_name}' not approved by user"

        return None  # Allowed

    # ── Helpers ──

    @staticmethod
    def _matches(tool_name: str, patterns: List[str]) -> bool:
        return any(fnmatch.fnmatch(tool_name, p) for p in patterns)

    def reset(self):
        self._deny_rules.clear()
        self._allow_rules.clear()
        self._mode = GateMode.INTERACTIVE


def _default_terminal_approve(tool_name: str, args: Dict) -> bool:
    """Fallback approval via terminal input(). Override via approval_callback."""
    msg = f"[Permission] Tool '{tool_name}' requests approval. Args: {args}\nAllow? [y/N] "
    print(msg, end="")
    response = input().strip().lower()
    return response == "y"


# ── App-level policy ──


def build_permissions() -> PermissionGate:
    """Build the coding agent permission gate.

    Policy:
        Deny:  destructive git ops
        Allow: read-only tools, memory, skills, subagent
        Ask:   everything else (write ops, shell, git mutations)
    """
    gate = PermissionGate(mode=GateMode.INTERACTIVE)

    # Step 1: deny — always blocked
    gate.deny("git_reset")
    gate.deny("git_revert")

    # Step 3: allow — always pass
    gate.allow("file_read")
    gate.allow("file_list")
    gate.allow("file_find")
    gate.allow("search_code")
    gate.allow("list_files")
    gate.allow("git_status")
    gate.allow("git_diff")
    gate.allow("git_log")
    gate.allow("git_show")
    gate.allow("git_branch_list")
    gate.allow("git_remote_list")
    gate.allow("git_stash_list")
    gate.allow("sys_info")
    gate.allow("save_memory")
    gate.allow("recall_memory")
    gate.allow("list_memories")
    gate.allow("skill")
    gate.allow("task")

    # Step 4: everything else → ask user
    # file_write, file_edit, shell_exec, git_commit, git_push, etc.

    return gate
