import json

import pytest

import main as nano_openclaw_main
from nanoharness.core.schema import LLMResponse, ToolCall
from nanoharness.profiles import HarnessBuilder, HarnessSpec


def test_profile_composes_only_public_durable_channel_extension():
    spec = HarnessSpec.from_file(str(nano_openclaw_main._HERE / "profile.yaml"))
    validation = HarnessBuilder().validate(spec)

    assert validation.valid is True
    assert [item.name for item in spec.extensions if item.enabled] == [
        "channels.durable"
    ]
    assert spec.metadata["boundary"] == "normalized-envelope-no-http-server"
    assert spec.engine.policy_service == "policy.gateway"
    assert spec.engine.approval_broker_service is None


def test_default_cli_runs_deterministic_batch(tmp_path, capsys):
    code = nano_openclaw_main.main(["--output", str(tmp_path)])
    payload = json.loads(capsys.readouterr().out)

    assert code == 0
    assert payload["success"] is True
    assert payload["processed"] == 2
    assert payload["delivered"] == 2


def test_cli_can_reject_outbound_delivery(tmp_path, capsys):
    code = nano_openclaw_main.main([
        "--output",
        str(tmp_path),
        "--approval",
        "deny",
    ])
    payload = json.loads(capsys.readouterr().out)

    assert code == 0
    assert payload["delivered"] == 0
    assert {turn["delivery_status"] for turn in payload["turns"]} == {"rejected"}


def test_openai_cli_requires_model(tmp_path):
    with pytest.raises(SystemExit, match="2"):
        nano_openclaw_main.main([
            "--output",
            str(tmp_path),
            "--provider",
            "openai",
        ])


def test_task_requires_real_provider(tmp_path):
    with pytest.raises(SystemExit, match="2"):
        nano_openclaw_main.main([
            "--output",
            str(tmp_path),
            "--task",
            "hello",
        ])


def test_real_provider_boundary_is_injectable(tmp_path, capsys, monkeypatch):
    calls = []

    class FakeProvider:
        def __init__(self, model, *, api_key=None, base_url=None):
            calls.append((model, api_key, base_url))
            self.index = 0

        def chat(self, messages, tools=None):
            self.index += 1
            if self.index == 1:
                return LLMResponse(
                    content="ready",
                    tool_calls=[ToolCall(
                        name="response_submit",
                        arguments={"answer": "live answer"},
                    )],
                )
            return LLMResponse(content="submitted")

    monkeypatch.setattr(nano_openclaw_main, "OpenAIChatProvider", FakeProvider)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    code = nano_openclaw_main.main([
        "--output",
        str(tmp_path),
        "--provider",
        "openai",
        "--model",
        "test-model",
        "--base-url",
        "https://provider.invalid/v1",
        "--task",
        "live question",
        "--approval",
        "auto",
    ])
    payload = json.loads(capsys.readouterr().out)

    assert code == 0
    assert payload["turns"][0]["response"] == "live answer"
    assert calls == [
        ("test-model", "test-key", "https://provider.invalid/v1")
    ]
