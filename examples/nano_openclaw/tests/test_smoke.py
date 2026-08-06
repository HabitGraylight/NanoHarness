from pathlib import Path

from main import run_demo


def test_nano_openclaw_runs_its_own_profile(tmp_path):
    result = run_demo(tmp_path)

    assert result.profile == "nano-openclaw"
    assert result.success is True
    assert result.tools == ["workspace_read"]
    assert Path(result.artifact.trace_path).exists()
