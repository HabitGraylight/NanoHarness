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
        assert len(reg.get_tool_schemas()) == 26

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

    def test_schema_and_argument_validation(self):
        reg = ScriptToolRegistry(SCRIPTS_DIR)
        for schema in reg.get_tool_schemas():
            assert "parameters" in schema["function"]

        with pytest.raises(ValueError, match="Protected environment"):
            reg.call("sys_info", {"section": "cwd", "PATH": "/tmp"})

        with pytest.raises(ValueError, match="Unexpected parameter"):
            reg.call("sys_info", {"section": "cwd", "undeclared": "value"})

        with pytest.raises(ValueError, match="Missing required parameter"):
            reg.call("file_read", {})


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

    @pytest.mark.parametrize(
        "scripts_dir",
        [SCRIPTS_DIR, "examples/nano_claude_code/configs/scripts"],
    )
    def test_file_edit_replaces_literal_not_earlier_regex_match(
        self,
        tmp_path,
        scripts_dir,
    ):
        path = tmp_path / "literal.txt"
        path.write_text(
            "result = userXgetName()\nresult = user.getName()\n",
            encoding="utf-8",
        )
        registry = ScriptToolRegistry(scripts_dir)
        registry.call("file_edit", {
            "path": str(path),
            "old_text": "user.getName()",
            "new_text": "account.getName()",
        })
        assert path.read_text(encoding="utf-8") == (
            "result = userXgetName()\nresult = account.getName()\n"
        )

    @pytest.mark.parametrize(
        "literal,decoy",
        [
            ("items[0]", "items0"),
            ("total*count", "totacount"),
            ("^start", "start"),
            ("end$", "end"),
            (r"a\+b", "aaab"),
            ("left|right", "leftXright"),
            ("group.(value)", "groupXvalue"),
        ],
    )
    def test_file_edit_treats_regex_metacharacters_as_literals(
        self,
        tmp_path,
        literal,
        decoy,
    ):
        path = tmp_path / "metacharacters.txt"
        path.write_text(f"{decoy}\n{literal}\n", encoding="utf-8")
        registry = ScriptToolRegistry(SCRIPTS_DIR)
        registry.call("file_edit", {
            "path": str(path),
            "old_text": literal,
            "new_text": "REPLACED",
        })
        assert path.read_text(encoding="utf-8") == f"{decoy}\nREPLACED\n"

    def test_file_edit_replacement_text_is_literal(self, tmp_path):
        path = tmp_path / "replacement.txt"
        path.write_text("before TARGET after", encoding="utf-8")
        replacement = "left&right|path\\name\nsecond line"
        registry = ScriptToolRegistry(SCRIPTS_DIR)
        registry.call("file_edit", {
            "path": str(path),
            "old_text": "TARGET",
            "new_text": replacement,
        })
        assert path.read_text(encoding="utf-8") == f"before {replacement} after"

    def test_file_edit_replace_all_counts_literal_occurrences(self, tmp_path):
        path = tmp_path / "replace-all.txt"
        path.write_text("a.b aXb a.b\na.b\n", encoding="utf-8")
        registry = ScriptToolRegistry(SCRIPTS_DIR)
        result = registry.call("file_edit", {
            "path": str(path),
            "old_text": "a.b",
            "new_text": "done",
            "replace_all": True,
        })
        assert result == f"Replaced 3 occurrences in {path}"
        assert path.read_text(encoding="utf-8") == "done aXb done\ndone\n"

    def test_file_edit_supports_multiline_literal_and_preserves_crlf(self, tmp_path):
        path = tmp_path / "multiline.txt"
        path.write_bytes(b"before\r\nalpha.*\r\nbeta[0]\r\nafter\r\n")
        registry = ScriptToolRegistry(SCRIPTS_DIR)
        registry.call("file_edit", {
            "path": str(path),
            "old_text": "alpha.*\r\nbeta[0]",
            "new_text": "replacement\r\nblock",
        })
        assert path.read_bytes() == b"before\r\nreplacement\r\nblock\r\nafter\r\n"

    def test_file_edit_not_found_keeps_original_bytes(self, tmp_path):
        path = tmp_path / "unchanged.txt"
        original = b"keep exactly\r\n"
        path.write_bytes(original)
        registry = ScriptToolRegistry(SCRIPTS_DIR)
        with pytest.raises(RuntimeError, match="old_text not found"):
            registry.call("file_edit", {
                "path": str(path),
                "old_text": "missing",
                "new_text": "replacement",
            })
        assert path.read_bytes() == original

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
