from pathlib import Path

from main import main


def test_run_rejects_missing_api_key_before_creating_worktree(
    tmp_path,
    monkeypatch,
    capsys,
):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    config = Path(__file__).resolve().parents[2] / "configs" / "loops" / "local_fix.yaml"
    runtime = tmp_path / "runtime"

    exit_code = main(
        [
            "--runtime-dir",
            str(runtime),
            "run",
            str(config),
            "--repo",
            str(tmp_path),
            "--task",
            "fix it",
        ]
    )

    assert exit_code == 1
    assert "DEEPSEEK_API_KEY" in capsys.readouterr().err
    assert not (runtime / "worktrees").exists()
