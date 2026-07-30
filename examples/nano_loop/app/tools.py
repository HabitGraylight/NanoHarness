"""A small, workspace-confined tool surface for NanoLoop workers."""

import re
from pathlib import Path

from nanoharness.components.tools.dict_registry import DictToolRegistry


class WorkspaceToolRegistry(DictToolRegistry):
    """File inspection and editing tools confined to one worktree."""

    def __init__(self, workspace_root: str):
        super().__init__()
        self.root = Path(workspace_root).resolve()
        if not self.root.is_dir():
            raise ValueError(f"Workspace does not exist: {self.root}")
        self._register_tools()

    def _register_tools(self) -> None:
        @self.tool
        def file_read(path: str, start_line: int = 1, end_line: int = 400) -> str:
            """Read a UTF-8 text file with one-based line bounds."""
            target = self._safe_path(path)
            if not target.is_file():
                raise FileNotFoundError(path)
            if start_line < 1 or end_line < start_line:
                raise ValueError("Invalid line range")
            lines = target.read_text(encoding="utf-8").splitlines()
            selected = lines[start_line - 1:end_line]
            return "\n".join(
                f"{number}: {line}"
                for number, line in enumerate(selected, start=start_line)
            )

        @self.tool
        def file_write(path: str, content: str) -> str:
            """Create or replace a UTF-8 text file inside the workspace."""
            target = self._safe_path(path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            return f"Wrote {len(content)} characters to {target.relative_to(self.root)}"

        @self.tool
        def file_edit(
            path: str,
            old_text: str,
            new_text: str,
            replace_all: bool = False,
        ) -> str:
            """Replace exact text in a workspace file."""
            target = self._safe_path(path)
            if not target.is_file():
                raise FileNotFoundError(path)
            content = target.read_text(encoding="utf-8")
            count = content.count(old_text)
            if count == 0:
                raise ValueError("old_text was not found")
            if count > 1 and not replace_all:
                raise ValueError(
                    f"old_text occurs {count} times; set replace_all=true or provide more context"
                )
            updated = content.replace(old_text, new_text, -1 if replace_all else 1)
            target.write_text(updated, encoding="utf-8")
            replacements = count if replace_all else 1
            return f"Replaced {replacements} occurrence(s) in {target.relative_to(self.root)}"

        @self.tool
        def list_files(pattern: str = "**/*") -> str:
            """List up to 200 files matching a workspace-relative glob."""
            self._validate_glob(pattern)
            matches = []
            for candidate in self.root.glob(pattern):
                resolved = candidate.resolve()
                if (
                    resolved.is_file()
                    and resolved.is_relative_to(self.root)
                    and ".git" not in resolved.relative_to(self.root).parts
                ):
                    matches.append(str(resolved.relative_to(self.root)))
                if len(matches) >= 200:
                    break
            return "\n".join(sorted(matches)) or "No files matched."

        @self.tool
        def search_code(
            pattern: str,
            path: str = ".",
            file_glob: str = "*.py",
        ) -> str:
            """Search text files with a regular expression and return 100 matches."""
            directory = self._safe_path(path, allow_root=True)
            if not directory.is_dir():
                raise NotADirectoryError(path)
            self._validate_glob(file_glob)
            expression = re.compile(pattern)
            matches = []
            for candidate in directory.rglob(file_glob):
                resolved = candidate.resolve()
                if (
                    not resolved.is_file()
                    or not resolved.is_relative_to(self.root)
                    or ".git" in resolved.relative_to(self.root).parts
                ):
                    continue
                try:
                    lines = resolved.read_text(encoding="utf-8").splitlines()
                except (UnicodeDecodeError, OSError):
                    continue
                for line_number, line in enumerate(lines, start=1):
                    if expression.search(line):
                        relative = resolved.relative_to(self.root)
                        matches.append(f"{relative}:{line_number}:{line}")
                        if len(matches) >= 100:
                            return "\n".join(matches)
            return "\n".join(matches) or "No matches found."

    def _safe_path(self, path: str, allow_root: bool = False) -> Path:
        raw = Path(path)
        target = raw if raw.is_absolute() else self.root / raw
        resolved = target.resolve()
        if not resolved.is_relative_to(self.root):
            raise PermissionError(f"Path escapes workspace: {path}")
        relative = resolved.relative_to(self.root)
        if ".git" in relative.parts:
            raise PermissionError("Direct access to .git is not allowed")
        if resolved == self.root and not allow_root:
            raise PermissionError("A file path is required")
        return resolved

    @staticmethod
    def _validate_glob(pattern: str) -> None:
        glob_path = Path(pattern)
        if glob_path.is_absolute() or ".." in glob_path.parts:
            raise PermissionError(f"Glob escapes workspace: {pattern}")


def build_workspace_tools(workspace_root: str) -> WorkspaceToolRegistry:
    return WorkspaceToolRegistry(workspace_root)
