import json
from types import SimpleNamespace

import pytest

import main as nano_hermes_main

from app.models import HermesJob


def test_scripted_cli_runs_learning_demo(tmp_path, capsys):
    exit_code = nano_hermes_main.main([
        "--output",
        str(tmp_path / "run"),
        "--approval",
        "auto",
        "--learning-approval",
        "auto",
    ])

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["profile"] == "nano-hermes"
    assert payload["success"] is True
    assert payload["promoted"] == [
        "memory:durable-learning-boundary",
        "skill:review-durable-learning",
    ]


def test_openai_cli_requires_model(tmp_path):
    with pytest.raises(SystemExit, match="2"):
        nano_hermes_main.main([
            "--provider",
            "openai",
            "--output",
            str(tmp_path / "run"),
        ])


def test_scripted_cli_rejects_task_override(tmp_path):
    with pytest.raises(SystemExit, match="2"):
        nano_hermes_main.main([
            "--task",
            "remember this",
            "--output",
            str(tmp_path / "run"),
        ])


def test_live_job_has_no_scripts_or_fixtures():
    job = HermesJob.from_file(nano_hermes_main._HERE / "jobs" / "live.yaml")
    assert job.scripted is False
    assert job.fixture_files == {}


def test_openai_cli_wires_provider_and_independent_approvals(monkeypatch, tmp_path):
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
                model_dump=lambda mode: {"profile": "nano-hermes", "success": True},
            )

    monkeypatch.setattr(nano_hermes_main, "OpenAIChatProvider", FakeProvider)
    monkeypatch.setattr(nano_hermes_main, "HermesHost", FakeHost)

    exit_code = nano_hermes_main.main([
        "--provider",
        "openai",
        "--model",
        "test-model",
        "--base-url",
        "https://provider.invalid/v1",
        "--job",
        str(nano_hermes_main._HERE / "jobs" / "live.yaml"),
        "--output",
        str(tmp_path / "run"),
        "--task",
        "Remember this preference",
        "--approval",
        "deny",
        "--learning-approval",
        "auto",
    ])

    assert exit_code == 0
    assert captured["provider"] == (
        "test-model",
        {"api_key": None, "base_url": "https://provider.invalid/v1"},
    )
    assert captured["job"].query == "Remember this preference"
    assert captured["host"]["approve_actions"] is False
    assert captured["host"]["approve_learning"] is True


def test_run_due_prints_result_list(monkeypatch, tmp_path, capsys):
    class FakeHost:
        def __init__(self, job, root, **kwargs):
            pass

        def run_due(self, job):
            return [SimpleNamespace(
                success=True,
                model_dump=lambda mode: {"run_kind": "scheduled", "success": True},
            )]

    monkeypatch.setattr(nano_hermes_main, "HermesHost", FakeHost)

    exit_code = nano_hermes_main.main([
        "--run-due",
        "--output",
        str(tmp_path / "run"),
    ])

    assert exit_code == 0
    assert json.loads(capsys.readouterr().out) == [
        {"run_kind": "scheduled", "success": True}
    ]
