import json
from types import SimpleNamespace

import pytest

import main as nano_codex_main
from app.models import CodexJob


def test_scripted_cli_runs_demo_and_prints_json(tmp_path, capsys):
    exit_code = nano_codex_main.main([
        "--output",
        str(tmp_path / "run"),
        "--approval",
        "auto",
    ])

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["profile"] == "nano-codex"
    assert payload["success"] is True
    assert payload["delivery_mode"] == "commit"


def test_openai_cli_requires_model(tmp_path):
    with pytest.raises(SystemExit, match="2"):
        nano_codex_main.main([
            "--provider",
            "openai",
            "--output",
            str(tmp_path / "run"),
        ])


def test_scripted_cli_rejects_task_override(tmp_path):
    with pytest.raises(SystemExit, match="2"):
        nano_codex_main.main([
            "--task",
            "change the project",
            "--output",
            str(tmp_path / "run"),
        ])


def test_live_job_is_provider_driven_and_repository_safe():
    job = CodexJob.from_file(nano_codex_main._HERE / "jobs" / "live.yaml")

    assert job.scripted is False
    assert job.fixture_files == {}
    assert {mode.value for mode in job.allowed_deliveries} == {
        "keep",
        "commit",
        "apply",
        "merge",
    }


def test_openai_cli_wires_live_repository_provider(monkeypatch, tmp_path):
    captured = {}

    class FakeProvider:
        def __init__(self, model, **kwargs):
            captured["provider"] = (model, kwargs)

    class FakeHost:
        def __init__(self, job, root, **kwargs):
            captured["job"] = job
            captured["root"] = root
            captured["host"] = kwargs

        def run(self):
            return SimpleNamespace(
                success=True,
                model_dump=lambda mode: {"profile": "nano-codex", "success": True},
            )

    monkeypatch.setattr(nano_codex_main, "OpenAIChatProvider", FakeProvider)
    monkeypatch.setattr(nano_codex_main, "CodexHost", FakeHost)
    live_job = nano_codex_main._HERE / "jobs" / "live.yaml"

    exit_code = nano_codex_main.main([
        "--provider",
        "openai",
        "--model",
        "test-model",
        "--base-url",
        "https://provider.invalid/v1",
        "--job",
        str(live_job),
        "--repo",
        str(tmp_path / "source"),
        "--output",
        str(tmp_path / "run"),
        "--task",
        "Implement the requested change",
        "--approval",
        "auto",
    ])

    assert exit_code == 0
    assert captured["provider"] == (
        "test-model",
        {"api_key": None, "base_url": "https://provider.invalid/v1"},
    )
    assert captured["job"].objective == "Implement the requested change"
    assert captured["host"]["repository"] == str(tmp_path / "source")
    assert callable(captured["host"]["provider_factory"])
    assert captured["host"]["approve_writes"] is True
