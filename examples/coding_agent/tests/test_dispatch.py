"""Tests for the dispatch layer: sandboxing, handlers, registry completeness."""

import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from app.dispatch import DispatchRegistry, bash_handler, bash_wrap, inprocess_handler, tool_result


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
        """call() auto-validates declared path params."""
        reg = DispatchRegistry(workspace_root=str(tmp_path))
        handler = inprocess_handler(lambda path: f"got {path}")
        reg.register("test_tool", handler, schema={}, path_params=["path"])

        # Relative subpath should work
        result = reg.call("test_tool", {"path": "src/main.py"})
        assert str(tmp_path) in result

        # Escape should fail
        with pytest.raises(PermissionError):
            reg.call("test_tool", {"path": "../../etc/passwd"})

    def test_no_path_params_skips_sandbox(self, tmp_path):
        """Tools without path_params skip sandbox validation."""
        reg = DispatchRegistry(workspace_root=str(tmp_path))
        handler = inprocess_handler(lambda: "ok")
        reg.register("no_path_tool", handler, schema={}, path_params=[])
        result = reg.call("no_path_tool", {})
        assert result == "ok"


# ── tool_result ──


class TestToolResult:
    def test_ok_result(self):
        r = tool_result(ok=True, output="hello")
        assert r.ok
        assert r.output == "hello"
        assert r.error is None

    def test_error_result(self):
        r = tool_result(ok=False, output="", error="boom")
        assert not r.ok
        assert r.error == "boom"


# ── bash_handler ──


class TestBashHandler:
    def test_success(self, tmp_path):
        script = tmp_path / "echo.sh"
        script.write_text('#!/bin/bash\necho "hello"\n')
        handler = bash_handler(str(script))
        result = handler({})
        assert result.ok
        assert result.output == "hello"

    def test_failure(self, tmp_path):
        script = tmp_path / "fail.sh"
        script.write_text('#!/bin/bash\necho "nope" >&2\nexit 1\n')
        handler = bash_handler(str(script))
        result = handler({})
        assert not result.ok
        assert "nope" in result.error

    def test_timeout(self, tmp_path):
        script = tmp_path / "slow.sh"
        script.write_text('#!/bin/bash\nsleep 10\n')
        handler = bash_handler(str(script), timeout=1)
        result = handler({})
        assert not result.ok
        assert "Timeout" in result.error

    def test_env_vars_passed(self, tmp_path):
        script = tmp_path / "env.sh"
        script.write_text('#!/bin/bash\necho "$MY_VAR"\n')
        handler = bash_handler(str(script))
        result = handler({"MY_VAR": "test_value"})
        assert result.ok
        assert result.output == "test_value"

    def test_bool_env_vars(self, tmp_path):
        script = tmp_path / "bool.sh"
        script.write_text('#!/bin/bash\necho "$FLAG"\n')
        handler = bash_handler(str(script))
        result = handler({"FLAG": True})
        assert result.output == "true"


# ── bash_wrap ──


class TestBashWrap:
    def test_success(self):
        handler = bash_wrap(lambda args: ["echo", "wrapped"])
        result = handler({})
        assert result.ok
        assert result.output == "wrapped"

    def test_failure_with_stderr(self):
        def bad_cmd(args):
            return ["bash", "-c", "echo oops >&2; exit 1"]
        handler = bash_wrap(bad_cmd)
        result = handler({})
        assert not result.ok
        assert "oops" in result.error


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


# ── Registry integration ──


class TestDispatchRegistryIntegration:
    def test_unknown_tool_raises(self, tmp_path):
        reg = DispatchRegistry(workspace_root=str(tmp_path))
        with pytest.raises(KeyError, match="not found"):
            reg.call("nonexistent", {})

    def test_error_handler_raises_runtime_error(self, tmp_path):
        reg = DispatchRegistry(workspace_root=str(tmp_path))
        handler = inprocess_handler(lambda: (_ for _ in ()).throw(ValueError("fail")))
        # Use a simpler approach: a handler that returns error result
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

    def test_full_tool_load(self):
        """All script + python tools register successfully."""
        from app.tools import build_tools
        scripts_dir = os.path.join(os.path.dirname(__file__), "..", "configs", "scripts")
        workspace = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        reg = build_tools(scripts_dir=scripts_dir, workspace_root=workspace)
        assert len(reg.dispatch_map) >= 26  # 26 scripts + 2 python = 28
        names = list(reg.dispatch_map.keys())
        assert "file_read" in names
        assert "file_edit" in names
        assert "search_code" in names
        assert "list_files" in names
        assert "git_status" in names
        assert "shell_exec" in names

    def test_shell_exec_no_path_validation(self):
        """shell_exec should have empty path_params (command is not a path)."""
        from app.tools import build_tools
        scripts_dir = os.path.join(os.path.dirname(__file__), "..", "configs", "scripts")
        workspace = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        reg = build_tools(scripts_dir=scripts_dir, workspace_root=workspace)
        assert reg._path_params.get("shell_exec", []) == []
        assert reg._path_params.get("sys_info", []) == []

    def test_file_tools_have_path_params(self):
        """File tools declare 'path' as a path param."""
        from app.tools import build_tools
        scripts_dir = os.path.join(os.path.dirname(__file__), "..", "configs", "scripts")
        workspace = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        reg = build_tools(scripts_dir=scripts_dir, workspace_root=workspace)
        assert "path" in reg._path_params.get("file_read", [])
        assert "path" in reg._path_params.get("file_write", [])

    def test_git_tools_have_repo_path_params(self):
        """Git tools declare 'repo_path' as a path param."""
        from app.tools import build_tools
        scripts_dir = os.path.join(os.path.dirname(__file__), "..", "configs", "scripts")
        workspace = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        reg = build_tools(scripts_dir=scripts_dir, workspace_root=workspace)
        assert "repo_path" in reg._path_params.get("git_status", [])
        assert "repo_path" in reg._path_params.get("git_log", [])

    def test_schemas_valid_structure(self):
        """Every schema has required JSON Schema fields."""
        from app.tools import build_tools
        scripts_dir = os.path.join(os.path.dirname(__file__), "..", "configs", "scripts")
        workspace = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        reg = build_tools(scripts_dir=scripts_dir, workspace_root=workspace)
        for schema in reg.get_tool_schemas():
            assert schema["type"] == "function"
            fn = schema["function"]
            assert "name" in fn
            assert "description" in fn
            assert "parameters" in fn
            params = fn["parameters"]
            assert params["type"] == "object"
            assert "properties" in params
            assert "required" in params
