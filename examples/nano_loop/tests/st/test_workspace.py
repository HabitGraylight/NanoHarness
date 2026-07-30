import subprocess

from app.workspace import GitWorktreeWorkspace


def git(repo, *args):
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    )


def test_git_worktree_workspace_creates_isolated_branch(tmp_path):
    repository = tmp_path / "repo"
    repository.mkdir()
    git(repository, "init", "-b", "main")
    git(repository, "config", "user.email", "test@example.com")
    git(repository, "config", "user.name", "NanoLoop Test")
    (repository / "README.md").write_text("base\n", encoding="utf-8")
    git(repository, "add", "README.md")
    git(repository, "commit", "-m", "initial")

    # The CLI's default runtime directory can live inside the source repository.
    provider = GitWorktreeWorkspace(str(repository / ".nanoloop" / "worktrees"))
    handle = provider.create("run-1", str(repository), "HEAD")

    try:
        assert handle.owned is True
        assert handle.branch == "nanoloop/run-1"
        assert (
            repository / ".nanoloop" / "worktrees" / "run-1" / "README.md"
        ).exists()
        current = git(handle.path, "branch", "--show-current").stdout.strip()
        assert current == "nanoloop/run-1"
        assert provider.create("run-1", str(repository), "HEAD") == handle
    finally:
        git(repository, "worktree", "remove", handle.path)
        git(repository, "branch", "-D", handle.branch)
