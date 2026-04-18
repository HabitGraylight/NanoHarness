import fnmatch
from typing import Dict, List

from nanoharness.core.base import BasePermissionManager
from nanoharness.core.prompt import PromptManager
from nanoharness.core.schema import PermissionLevel, PermissionRule


class RulePermissionManager(BasePermissionManager):
    """Permission manager based on a list of rules.

    Supports glob patterns in tool names (e.g. "git_*", "*").
    First matching rule wins. If no rule matches, falls back to default_level.
    """

    def __init__(
        self,
        default_level: PermissionLevel = PermissionLevel.ALLOW,
        prompts: PromptManager = None,
    ):
        self._rules: List[PermissionRule] = []
        self._default_level = default_level
        self.prompts = prompts or PromptManager()

    def add_rule(self, rule: PermissionRule):
        self._rules.append(rule)

    def deny(self, tool_name: str, **blocked_params):
        """Convenience: deny a tool (optionally with blocked param values)."""
        bl = {k: v if isinstance(v, list) else [v] for k, v in blocked_params.items()}
        self._rules.append(
            PermissionRule(tool_name=tool_name, level=PermissionLevel.DENY, blocked_params=bl)
        )

    def confirm(self, tool_name: str, **blocked_params):
        """Convenience: require confirmation for a tool."""
        bl = {k: v if isinstance(v, list) else [v] for k, v in blocked_params.items()}
        self._rules.append(
            PermissionRule(tool_name=tool_name, level=PermissionLevel.CONFIRM, blocked_params=bl)
        )

    def check(self, tool_name: str, args: Dict) -> PermissionLevel:
        for rule in self._rules:
            if fnmatch.fnmatch(tool_name, rule.tool_name):
                # Check blocked params
                for param, blocked_values in rule.blocked_params.items():
                    if args.get(param) in blocked_values:
                        return PermissionLevel.DENY
                return rule.level
        return self._default_level

    def approve(self, tool_name: str, args: Dict) -> bool:
        """Interactive approval — override in subclasses for headless mode."""
        msg = self.prompts.render(
            "permission.interactive_confirm", tool_name=tool_name, args=args
        )
        print(msg, end="")
        response = input().strip().lower()
        return response == "y"

    def reset(self):
        self._rules.clear()
