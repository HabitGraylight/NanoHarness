from pathlib import Path

import pytest

from app.tools import WorkspaceToolRegistry


def test_workspace_tools_read_write_edit_search(tmp_path):
    registry = WorkspaceToolRegistry(str(tmp_path))
    registry.call("file_write", {"path": "pkg/demo.py", "content": "value = 1\n"})
    assert "value = 1" in registry.call("file_read", {"path": "pkg/demo.py"})

    registry.call(
        "file_edit",
        {
            "path": "pkg/demo.py",
            "old_text": "value = 1",
            "new_text": "value = 2",
        },
    )
    assert "pkg/demo.py" in registry.call("list_files", {"pattern": "**/*.py"})
    assert "value = 2" in registry.call(
        "search_code",
        {"pattern": "value", "path": "pkg", "file_glob": "*.py"},
    )


def test_workspace_tools_reject_path_escape(tmp_path):
    registry = WorkspaceToolRegistry(str(tmp_path))
    with pytest.raises(PermissionError):
        registry.call("file_write", {"path": "../outside.txt", "content": "no"})


def test_workspace_tools_reject_git_access(tmp_path):
    (tmp_path / ".git").mkdir()
    registry = WorkspaceToolRegistry(str(tmp_path))
    with pytest.raises(PermissionError):
        registry.call("file_read", {"path": ".git/config"})


def test_file_edit_requires_unique_match(tmp_path):
    target = tmp_path / "a.txt"
    target.write_text("same\nsame\n", encoding="utf-8")
    registry = WorkspaceToolRegistry(str(tmp_path))
    with pytest.raises(ValueError, match="occurs 2 times"):
        registry.call(
            "file_edit",
            {"path": "a.txt", "old_text": "same", "new_text": "new"},
        )
