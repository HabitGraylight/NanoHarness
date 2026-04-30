"""Tests for the dispatch layer: sandboxing, handlers, registry completeness."""

import os
import pytest

from app.dispatch import DispatchRegistry, inprocess_handler, tool_result


# ── Path sandbox ──


class TestSandbox:
    def test_blocks_escape_via_dotdot(self, tmp_path):
        reg = DispatchRegistry(workspace_root=str(tmp_path))
        with pytest.raises(PermissionError, match="escapes workspace"):
            reg.sandbox_path("../../etc/passwd")

    def test_blocks_absolute_outside(self, tmp_path):
        reg = DispatchRegistry(workspace_root=str(tmp_path))
        with pytest.raises(PermissionError, match="escapes workspace"):
            reg.sandbox_path("/etc/passwd")

    def test_allows_subpath(self, tmp_path):
        reg = DispatchRegistry(workspace_root=str(tmp_path))
        resolved = reg.sandbox_path("src/main.py")
        assert resolved.startswith(str(tmp_path))
        assert resolved.endswith("src/main.py")

    def test_allows_dot(self, tmp_path):
        reg = DispatchRegistry(workspace_root=str(tmp_path))
        resolved = reg.sandbox_path(".")
        assert resolved == str(tmp_path)

    def test_sandbox_in_call(self, tmp_path):
        reg = DispatchRegistry(workspace_root=str(tmp_path))
        handler = inprocess_handler(lambda path: f"got {path}")
        reg.register("test_tool", handler, schema={}, path_params=["path"])
        result = reg.call("test_tool", {"path": "src/main.py"})
        assert str(tmp_path) in result
        with pytest.raises(PermissionError):
            reg.call("test_tool", {"path": "../../etc/passwd"})

    def test_no_path_params_skips_sandbox(self, tmp_path):
        reg = DispatchRegistry(workspace_root=str(tmp_path))
        handler = inprocess_handler(lambda: "ok")
        reg.register("no_path_tool", handler, schema={}, path_params=[])
        result = reg.call("no_path_tool", {})
        assert result == "ok"


# ── tool_result ──


class Testtool_result:
    def test_ok_result(self):
        r = tool_result(ok=True, output="hello")
        assert r.ok
        assert r.output == "hello"
        assert r.error is None

    def test_error_result(self):
        r = tool_result(ok=False, output="", error="boom")
        assert not r.ok
        assert r.error == "boom"


# ── inprocess_handler ──


class TestInprocessHandler:
    def test_wraps_return(self):
        handler = inprocess_handler(lambda x: f"got {x}")
        result = handler({"x": "hello"})
        assert result.ok
        assert result.output == "got hello"

    def test_catches_exception(self):
        def boom(x):
            raise ValueError("kaboom")
        handler = inprocess_handler(boom)
        result = handler({"x": "test"})
        assert not result.ok
        assert "kaboom" in result.error


# ── Registry basic ops ──


class TestDispatchRegistryIntegration:
    def test_unknown_tool_raises(self, tmp_path):
        reg = DispatchRegistry(workspace_root=str(tmp_path))
        with pytest.raises(KeyError, match="not found"):
            reg.call("nonexistent", {})

    def test_error_handler_raises_runtime_error(self, tmp_path):
        reg = DispatchRegistry(workspace_root=str(tmp_path))
        reg.register("fail_tool", lambda args: tool_result(ok=False, output="", error="boom"), schema={})
        with pytest.raises(RuntimeError, match="boom"):
            reg.call("fail_tool", {})

    def test_get_tool_schemas(self, tmp_path):
        reg = DispatchRegistry(workspace_root=str(tmp_path))
        reg.register("t1", lambda a: tool_result(ok=True, output=""), schema={"type": "function", "function": {"name": "t1"}})
        reg.register("t2", lambda a: tool_result(ok=True, output=""), schema={"type": "function", "function": {"name": "t2"}})
        schemas = reg.get_tool_schemas()
        names = [s["function"]["name"] for s in schemas]
        assert "t1" in names
        assert "t2" in names

    def test_reset(self, tmp_path):
        reg = DispatchRegistry(workspace_root=str(tmp_path))
        reg.register("t", lambda a: tool_result(ok=True, output=""), schema={})
        assert len(reg.dispatch_map) == 1
        reg.reset()
        assert len(reg.dispatch_map) == 0
        assert len(reg.schemas) == 0
