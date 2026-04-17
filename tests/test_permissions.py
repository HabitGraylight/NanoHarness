from nanoharness.components.permissions.rule_permission import RulePermissionManager
from nanoharness.core.schema import PermissionLevel, PermissionRule


class TestRulePermissionManager:
    def test_default_allow(self):
        pm = RulePermissionManager()
        assert pm.check("any_tool", {}) == PermissionLevel.ALLOW

    def test_default_deny(self):
        pm = RulePermissionManager(default_level=PermissionLevel.DENY)
        assert pm.check("any_tool", {}) == PermissionLevel.DENY

    def test_exact_match(self):
        pm = RulePermissionManager()
        pm.add_rule(PermissionRule(tool_name="dangerous_op", level=PermissionLevel.DENY))
        assert pm.check("dangerous_op", {}) == PermissionLevel.DENY
        assert pm.check("safe_op", {}) == PermissionLevel.ALLOW

    def test_glob_match(self):
        pm = RulePermissionManager()
        pm.confirm("git_*")
        assert pm.check("git_commit", {}) == PermissionLevel.CONFIRM
        assert pm.check("git_push", {}) == PermissionLevel.CONFIRM
        assert pm.check("echo", {}) == PermissionLevel.ALLOW

    def test_wildcard_all(self):
        pm = RulePermissionManager(default_level=PermissionLevel.ALLOW)
        pm.deny("*")
        assert pm.check("anything", {}) == PermissionLevel.DENY

    def test_blocked_params(self):
        pm = RulePermissionManager()
        pm.add_rule(PermissionRule(
            tool_name="file_write",
            level=PermissionLevel.ALLOW,
            blocked_params={"path": ["/etc/passwd", "/etc/shadow"]},
        ))
        assert pm.check("file_write", {"path": "/home/user/f.txt"}) == PermissionLevel.ALLOW
        assert pm.check("file_write", {"path": "/etc/passwd"}) == PermissionLevel.DENY

    def test_first_match_wins(self):
        pm = RulePermissionManager()
        pm.add_rule(PermissionRule(tool_name="git_*", level=PermissionLevel.CONFIRM))
        pm.add_rule(PermissionRule(tool_name="git_status", level=PermissionLevel.ALLOW))
        # git_* matches first
        assert pm.check("git_status", {}) == PermissionLevel.CONFIRM

    def test_reset(self):
        pm = RulePermissionManager()
        pm.deny("x")
        assert pm.check("x", {}) == PermissionLevel.DENY
        pm.reset()
        assert pm.check("x", {}) == PermissionLevel.ALLOW
