"""ST for dispatch layer — bash handlers, full tool load."""

import os
import pytest

from app.dispatch import DispatchRegistry, bash_handler, bash_wrap, tool_result


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


# ── Full tool load ──


class TestFullToolLoad:
    def test_full_tool_load(self):
        from app.tools import build_tools
        scripts_dir = os.path.join(os.path.dirname(__file__), "..", "..", "configs", "scripts")
        workspace = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
        reg = build_tools(scripts_dir=scripts_dir, workspace_root=workspace)
        assert len(reg.dispatch_map) >= 26
        names = list(reg.dispatch_map.keys())
        assert "file_read" in names
        assert "file_edit" in names
        assert "search_code" in names
        assert "list_files" in names
        assert "git_status" in names
        assert "shell_exec" in names

    def test_shell_exec_no_path_validation(self):
        from app.tools import build_tools
        scripts_dir = os.path.join(os.path.dirname(__file__), "..", "..", "configs", "scripts")
        workspace = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
        reg = build_tools(scripts_dir=scripts_dir, workspace_root=workspace)
        assert reg._path_params.get("shell_exec", []) == []
        assert reg._path_params.get("sys_info", []) == []

    def test_file_tools_have_path_params(self):
        from app.tools import build_tools
        scripts_dir = os.path.join(os.path.dirname(__file__), "..", "..", "configs", "scripts")
        workspace = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
        reg = build_tools(scripts_dir=scripts_dir, workspace_root=workspace)
        assert "path" in reg._path_params.get("file_read", [])
        assert "path" in reg._path_params.get("file_write", [])

    def test_git_tools_have_repo_path_params(self):
        from app.tools import build_tools
        scripts_dir = os.path.join(os.path.dirname(__file__), "..", "..", "configs", "scripts")
        workspace = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
        reg = build_tools(scripts_dir=scripts_dir, workspace_root=workspace)
        assert "repo_path" in reg._path_params.get("git_status", [])
        assert "repo_path" in reg._path_params.get("git_log", [])

    def test_schemas_valid_structure(self):
        from app.tools import build_tools
        scripts_dir = os.path.join(os.path.dirname(__file__), "..", "..", "configs", "scripts")
        workspace = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
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
