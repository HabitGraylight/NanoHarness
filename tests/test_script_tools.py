import subprocess

import pytest

from nanoharness.components.tools.script_tools import ScriptToolRegistry


@pytest.fixture
def git_repo(tmp_path):
    """Create a temporary git repo with one initial commit."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.email", "test@test.com"],
        check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.name", "Test"],
        check=True, capture_output=True,
    )
    (repo / "hello.txt").write_text("hello")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-m", "init"],
        check=True, capture_output=True,
    )
    return str(repo)


SCRIPTS_DIR = "configs/scripts"


class TestScriptRegistryLoading:
    def test_loads_all_scripts(self):
        reg = ScriptToolRegistry(SCRIPTS_DIR)
        assert len(reg.get_tool_schemas()) == 27

    def test_git_tools_present(self):
        reg = ScriptToolRegistry(SCRIPTS_DIR)
        names = [s["function"]["name"] for s in reg.get_tool_schemas()]
        for t in ["git_status", "git_log", "git_commit", "git_push"]:
            assert t in names

    def test_file_tools_present(self):
        reg = ScriptToolRegistry(SCRIPTS_DIR)
        names = [s["function"]["name"] for s in reg.get_tool_schemas()]
        for t in ["file_read", "file_write", "file_edit", "file_list", "file_find"]:
            assert t in names

    def test_system_tools_present(self):
        reg = ScriptToolRegistry(SCRIPTS_DIR)
        names = [s["function"]["name"] for s in reg.get_tool_schemas()]
        for t in ["sys_info", "shell_exec"]:
            assert t in names

    def test_you_search_tool_present(self):
        reg = ScriptToolRegistry(SCRIPTS_DIR)
        names = [s["function"]["name"] for s in reg.get_tool_schemas()]
        assert "you_search" in names

    def test_schema_has_parameters(self):
        reg = ScriptToolRegistry(SCRIPTS_DIR)
        for schema in reg.get_tool_schemas():
            assert "parameters" in schema["function"]


class TestGitToolsViaScripts:
    def test_status(self, git_repo):
        reg = ScriptToolRegistry(SCRIPTS_DIR)
        result = reg.call("git_status", {"repo_path": git_repo})
        assert "branch" in result or "分支" in result

    def test_log(self, git_repo):
        reg = ScriptToolRegistry(SCRIPTS_DIR)
        result = reg.call("git_log", {"repo_path": git_repo})
        assert "init" in result

    def test_show(self, git_repo):
        reg = ScriptToolRegistry(SCRIPTS_DIR)
        result = reg.call("git_show", {"repo_path": git_repo, "revision": "HEAD"})
        assert "init" in result

    def test_branch_list(self, git_repo):
        reg = ScriptToolRegistry(SCRIPTS_DIR)
        result = reg.call("git_branch_list", {"repo_path": git_repo})
        assert "main" in result or "master" in result

    def test_add_and_commit(self, git_repo):
        import pathlib
        pathlib.Path(git_repo, "new.txt").write_text("new file")

        reg = ScriptToolRegistry(SCRIPTS_DIR)
        reg.call("git_add", {"repo_path": git_repo, "files": "."})
        result = reg.call("git_commit", {"repo_path": git_repo, "message": "add new"})
        assert "add new" in result

    def test_branch_create_and_checkout(self, git_repo):
        reg = ScriptToolRegistry(SCRIPTS_DIR)
        reg.call("git_branch_create", {"repo_path": git_repo, "name": "feature"})
        reg.call("git_checkout", {"repo_path": git_repo, "branch": "feature"})
        result = reg.call("git_branch_list", {"repo_path": git_repo})
        assert "feature" in result

    def test_stash(self, git_repo):
        import pathlib
        pathlib.Path(git_repo, "hello.txt").write_text("modified")

        reg = ScriptToolRegistry(SCRIPTS_DIR)
        reg.call("git_add", {"repo_path": git_repo, "files": "."})
        reg.call("git_stash", {"repo_path": git_repo, "message": "wip"})
        stash_list = reg.call("git_stash_list", {"repo_path": git_repo})
        assert "wip" in stash_list


class TestFileToolsViaScripts:
    def test_file_write_and_read(self, tmp_path):
        reg = ScriptToolRegistry(SCRIPTS_DIR)
        fpath = str(tmp_path / "test.txt")

        reg.call("file_write", {"path": fpath, "content": "hello world"})
        result = reg.call("file_read", {"path": fpath})
        assert "hello world" in result

    def test_file_edit(self, tmp_path):
        reg = ScriptToolRegistry(SCRIPTS_DIR)
        fpath = str(tmp_path / "edit.txt")

        reg.call("file_write", {"path": fpath, "content": "foo bar baz"})
        reg.call("file_edit", {
            "path": fpath,
            "old_text": "bar",
            "new_text": "QUX",
        })
        result = reg.call("file_read", {"path": fpath})
        assert "QUX" in result

    def test_file_list(self, tmp_path):
        reg = ScriptToolRegistry(SCRIPTS_DIR)
        (tmp_path / "a.txt").write_text("a")
        (tmp_path / "b.txt").write_text("b")

        result = reg.call("file_list", {"path": str(tmp_path)})
        assert "a.txt" in result
        assert "b.txt" in result

    def test_file_find(self, tmp_path):
        reg = ScriptToolRegistry(SCRIPTS_DIR)
        (tmp_path / "data.csv").write_text("x")
        (tmp_path / "data.json").write_text("{}")

        result = reg.call("file_find", {"path": str(tmp_path), "pattern": "*.json"})
        assert "data.json" in result
        assert "data.csv" not in result

    def test_file_read_not_found(self):
        reg = ScriptToolRegistry(SCRIPTS_DIR)
        with pytest.raises(RuntimeError):
            reg.call("file_read", {"path": "/nonexistent/file.txt"})


class TestSystemToolsViaScripts:
    def test_sys_info(self):
        reg = ScriptToolRegistry(SCRIPTS_DIR)
        result = reg.call("sys_info", {"section": "cwd"})
        assert "CWD" in result

    def test_shell_exec(self):
        reg = ScriptToolRegistry(SCRIPTS_DIR)
        result = reg.call("shell_exec", {"command": "echo hello"})
        assert "hello" in result
