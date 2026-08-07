import sys
import subprocess
from pathlib import Path

import pytest


EXAMPLE_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = EXAMPLE_ROOT.parents[1]

sys.path.insert(0, str(REPOSITORY_ROOT))
sys.path.insert(0, str(EXAMPLE_ROOT))

from app.models import CodexJob


@pytest.fixture
def demo_job():
    return CodexJob.from_file(EXAMPLE_ROOT / "jobs" / "demo.yaml")


def initialize_git_repository(path: Path, files=None) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    for relative, content in (files or {"README.md": "# test\n"}).items():
        target = path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.name", "NanoCodex Test"],
        cwd=path,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "test@example.invalid"],
        cwd=path,
        check=True,
    )
    subprocess.run(["git", "add", "."], cwd=path, check=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=path, check=True)
    return path


@pytest.fixture
def git_repo(tmp_path):
    return initialize_git_repository(tmp_path / "source")
