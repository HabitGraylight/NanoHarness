"""Workspace strategies for isolated loop iterations."""

import subprocess
from pathlib import Path
from typing import Protocol

from app.schema import WorkspaceHandle


class WorkspaceProvider(Protocol):
    def create(self, run_id: str, repository: str, base_ref: str) -> WorkspaceHandle:
        ...


class LocalWorkspace:
    """Use an existing directory directly.

    This is useful for tests and intentionally opt-in configurations. Autonomous
    coding loops should normally use ``GitWorktreeWorkspace`` instead.
    """

    def create(self, run_id: str, repository: str, base_ref: str) -> WorkspaceHandle:
        path = Path(repository).resolve()
        if not path.is_dir():
            raise ValueError(f"Repository path does not exist: {path}")
        return WorkspaceHandle(path=str(path), owned=False)


class GitWorktreeWorkspace:
    """Create one disposable Git worktree and branch per loop run."""

    def __init__(self, worktrees_root: str):
        self.root = Path(worktrees_root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def create(self, run_id: str, repository: str, base_ref: str) -> WorkspaceHandle:
        repository_path = Path(repository).resolve()
        self._require_git_repository(repository_path)

        worktree_path = self.root / run_id
        if worktree_path.exists():
            recovered = self._recover_existing(worktree_path, run_id)
            if recovered:
                return recovered
            raise FileExistsError(f"Worktree path already exists: {worktree_path}")

        branch = f"nanoloop/{run_id}"
        result = subprocess.run(
            [
                "git",
                "worktree",
                "add",
                "-b",
                branch,
                str(worktree_path),
                base_ref,
            ],
            cwd=repository_path,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(
                "git worktree add failed: "
                + (result.stderr.strip() or result.stdout.strip())
            )

        return WorkspaceHandle(
            path=str(worktree_path),
            branch=branch,
            owned=True,
        )

    @staticmethod
    def _recover_existing(
        worktree_path: Path,
        run_id: str,
    ) -> WorkspaceHandle | None:
        """Recover the deterministic worktree after a prepare/save crash."""
        top = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=worktree_path,
            capture_output=True,
            text=True,
        )
        branch_result = subprocess.run(
            ["git", "branch", "--show-current"],
            cwd=worktree_path,
            capture_output=True,
            text=True,
        )
        expected_branch = f"nanoloop/{run_id}"
        if (
            top.returncode == 0
            and Path(top.stdout.strip()).resolve() == worktree_path.resolve()
            and branch_result.returncode == 0
            and branch_result.stdout.strip() == expected_branch
        ):
            return WorkspaceHandle(
                path=str(worktree_path),
                branch=expected_branch,
                owned=True,
            )
        return None

    @staticmethod
    def _require_git_repository(path: Path) -> None:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=path,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise ValueError(f"Not a Git repository: {path}")
