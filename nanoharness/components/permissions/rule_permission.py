import fnmatch
from typing import Callable, Dict, List, Optional

from nanoharness.core.base import BasePermissionManager
from nanoharness.core.schema import PermissionLevel, PermissionRule


class RulePermissionManager(BasePermissionManager):
    """Permission manager based on glob-pattern rules.

    - First matching rule wins.
    - If no rule matches, falls back to default_level.
    - Approval handling is decoupled: inject an approval_callback
      for your environment (terminal, API, GUI, etc.).
    """

    def __init__(
        self,
        default_level: PermissionLevel = PermissionLevel.ALLOW,
        approval_callback: Optional[Callable[[str, Dict], bool]] = None,
    ):
        self._rules: List[PermissionRule] = []
        self._default_level = default_level
        self._approval_callback = approval_callback

    def add_rule(self, rule: PermissionRule):
        self._rules.append(rule)

    def deny(self, tool_name: str, **blocked_params):
        """Deny a tool outright (optionally with blocked param values)."""
        bl = {k: v if isinstance(v, list) else [v] for k, v in blocked_params.items()}
        self._rules.append(
            PermissionRule(tool_name=tool_name, level=PermissionLevel.DENY, blocked_params=bl)
        )

    def confirm(self, tool_name: str, **blocked_params):
        """Require confirmation for a tool."""
        bl = {k: v if isinstance(v, list) else [v] for k, v in blocked_params.items()}
        self._rules.append(
            PermissionRule(tool_name=tool_name, level=PermissionLevel.CONFIRM, blocked_params=bl)
        )

    def check(self, tool_name: str, args: Dict) -> PermissionLevel:
        for rule in self._rules:
            if fnmatch.fnmatch(tool_name, rule.tool_name):
                for param, blocked_values in rule.blocked_params.items():
                    if args.get(param) in blocked_values:
                        return PermissionLevel.DENY
                return rule.level
        return self._default_level

    def enforce(self, tool_name: str, args: Dict) -> Optional[str]:
        """Check permissions and handle approval flow.

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

    def reset(self):
        self._rules.clear()


def _default_terminal_approve(tool_name: str, args: Dict) -> bool:
    """Fallback approval via terminal input(). Override via approval_callback."""
    msg = f"[Permission] Tool '{tool_name}' requests approval. Args: {args}\nAllow? [y/N] "
    print(msg, end="")
    response = input().strip().lower()
    return response == "y"
