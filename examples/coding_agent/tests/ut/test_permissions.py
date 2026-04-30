"""Tests for PermissionGate -- 4-step pipeline: deny -> mode -> allow -> ask."""
import pytest
from app.permissions import PermissionGate, GateMode, PermissionLevel, build_permissions


class TestDenyOverridesAllow:
    def test_deny_overrides_allow(self):
        """Deny rule takes priority over allow rule for same pattern."""
        gate = PermissionGate(mode=GateMode.AUTO)
        gate.allow("git_status")
        gate.deny("git_status")
        assert gate.check("git_status", {}) == PermissionLevel.DENY


class TestDenyGlob:
    def test_deny_glob(self):
        """Glob patterns like 'git_*' match correctly."""
        gate = PermissionGate(mode=GateMode.AUTO)
        gate.deny("git_*")
        assert gate.check("git_reset", {}) == PermissionLevel.DENY
        assert gate.check("git_push", {}) == PermissionLevel.DENY
        assert gate.check("git_log", {}) == PermissionLevel.DENY


class TestModeYolo:
    def test_mode_yolo(self):
        """YOLO mode passes everything not denied."""
        gate = PermissionGate(mode=GateMode.YOLO)
        # No allow rules, but YOLO passes anything not denied
        assert gate.check("anything_at_all", {}) == PermissionLevel.ALLOW
        assert gate.check("shell_exec", {}) == PermissionLevel.ALLOW

        # Deny still works in YOLO
        gate.deny("dangerous")
        assert gate.check("dangerous", {}) == PermissionLevel.DENY


class TestModeAuto:
    def test_mode_auto(self):
        """AUTO mode denies unlisted tools."""
        gate = PermissionGate(mode=GateMode.AUTO)
        gate.allow("file_read")
        assert gate.check("file_read", {}) == PermissionLevel.ALLOW
        assert gate.check("unknown_tool", {}) == PermissionLevel.DENY


class TestModeInteractive:
    def test_mode_interactive(self):
        """INTERACTIVE mode returns CONFIRM for unlisted tools."""
        gate = PermissionGate(mode=GateMode.INTERACTIVE)
        assert gate.check("some_new_tool", {}) == PermissionLevel.CONFIRM


class TestModeSwitch:
    def test_mode_switch(self):
        """set_mode() changes mode at runtime."""
        gate = PermissionGate(mode=GateMode.INTERACTIVE)
        assert gate.mode == GateMode.INTERACTIVE

        gate.set_mode(GateMode.YOLO)
        assert gate.mode == GateMode.YOLO
        assert gate.check("anything", {}) == PermissionLevel.ALLOW

        gate.set_mode(GateMode.AUTO)
        assert gate.mode == GateMode.AUTO
        assert gate.check("anything", {}) == PermissionLevel.DENY


class TestPipelineOrder:
    def test_pipeline_order(self):
        """Verify the 4-step pipeline runs in correct order.

        Step 1: deny rules checked first (overrides everything)
        Step 2: mode=YOLO passes all remaining
        Step 3: allow rules checked
        Step 4: fallback (interactive=CONFIRM, auto=DENY)
        """
        gate = PermissionGate(mode=GateMode.AUTO)
        gate.deny("forbidden")
        gate.allow("safe_tool")

        # Step 1: deny wins over allow
        gate.allow("forbidden")
        assert gate.check("forbidden", {}) == PermissionLevel.DENY

        # Step 3: allow rules pass
        assert gate.check("safe_tool", {}) == PermissionLevel.ALLOW

        # Step 4: auto mode denies unlisted
        assert gate.check("unknown", {}) == PermissionLevel.DENY


class TestApprovalCallback:
    def test_approval_callback(self):
        """enforce() uses approval_callback for interactive mode."""
        callback_calls = []

        def callback(tool_name, args):
            callback_calls.append((tool_name, args))
            return True  # approve

        gate = PermissionGate(mode=GateMode.INTERACTIVE, approval_callback=callback)
        # Unlisted tool in interactive mode triggers CONFIRM -> callback
        result = gate.enforce("new_tool", {"key": "val"})
        assert result is None  # allowed
        assert len(callback_calls) == 1
        assert callback_calls[0][0] == "new_tool"

    def test_approval_callback_rejects(self):
        """enforce() returns error when callback rejects."""
        def reject_all(tool_name, args):
            return False

        gate = PermissionGate(mode=GateMode.INTERACTIVE, approval_callback=reject_all)
        result = gate.enforce("new_tool", {})
        assert result is not None
        assert "not approved" in result


class TestBuildPermissionsPolicy:
    def test_build_permissions_policy(self):
        """build_permissions() returns gate with deny rules for git_reset, git_revert."""
        gate = build_permissions()
        assert gate.check("git_reset", {}) == PermissionLevel.DENY
        assert gate.check("git_revert", {}) == PermissionLevel.DENY


class TestBuildPermissionsAllowRules:
    def test_build_permissions_allow_rules(self):
        """build_permissions() allows file_read, search_code, memory, task, skill tools."""
        gate = build_permissions()
        assert gate.check("file_read", {}) == PermissionLevel.ALLOW
        assert gate.check("search_code", {}) == PermissionLevel.ALLOW
        assert gate.check("save_memory", {}) == PermissionLevel.ALLOW
        assert gate.check("recall_memory", {}) == PermissionLevel.ALLOW
        assert gate.check("task", {}) == PermissionLevel.ALLOW
        assert gate.check("skill", {}) == PermissionLevel.ALLOW


class TestBuildPermissionsMCPRules:
    def test_build_permissions_mcp_rules(self):
        """build_permissions() allows mcp__filesystem__* pattern."""
        gate = build_permissions()
        assert gate.check("mcp__filesystem__read", {}) == PermissionLevel.ALLOW
        assert gate.check("mcp__filesystem__write", {}) == PermissionLevel.ALLOW


class TestReset:
    def test_reset(self):
        """reset() clears all rules and resets mode."""
        gate = PermissionGate(mode=GateMode.AUTO)
        gate.deny("something")
        gate.allow("other")
        gate.set_mode(GateMode.YOLO)

        gate.reset()

        assert gate.mode == GateMode.INTERACTIVE
        # After reset, no rules -- interactive mode returns CONFIRM
        assert gate.check("something", {}) == PermissionLevel.CONFIRM
        assert gate.check("other", {}) == PermissionLevel.CONFIRM
