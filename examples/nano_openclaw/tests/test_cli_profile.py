import json

import pytest

import main as nano_openclaw_main
from nanoharness.core.schema import LLMResponse, ToolCall
from nanoharness.profiles import HarnessBuilder, HarnessSpec


def test_profile_composes_public_channel_and_scheduler_extensions():
    spec = HarnessSpec.from_file(str(nano_openclaw_main._HERE / "profile.yaml"))
    validation = HarnessBuilder().validate(spec)

    assert validation.valid is True
    assert [item.name for item in spec.extensions if item.enabled] == [
        "channels.durable",
        "scheduler.local",
    ]
    assert spec.metadata["boundary"] == "normalized-envelope-no-http-server"
    assert spec.engine.policy_service == "policy.gateway"
    assert spec.engine.approval_broker_service is None


def test_default_cli_runs_deterministic_batch(tmp_path, capsys):
    code = nano_openclaw_main.main(["--output", str(tmp_path)])
    payload = json.loads(capsys.readouterr().out)

    assert code == 0
    assert payload["success"] is True
    assert payload["processed"] == 3
    assert payload["delivered"] == 3


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
    assert payload["manual"]["turn"]["response"] == "live answer"
    assert calls == [
        ("test-model", "test-key", "https://provider.invalid/v1")
    ]


def test_cli_ingest_generate_inspect_and_deliver_are_separate_operations(
    tmp_path,
    capsys,
):
    job_path = nano_openclaw_main._HERE / "jobs" / "demo.yaml"
    job = nano_openclaw_main.GatewayJob.from_file(job_path)
    ingress_path = tmp_path / "inbound.json"
    ingress_path.write_text(json.dumps(
        job.messages[0].envelope.model_dump(mode="json")
    ))

    assert nano_openclaw_main.main([
        "--output", str(tmp_path / "run"),
        "--job", str(job_path),
        "--ingest", str(ingress_path),
    ]) == 0
    ingress = json.loads(capsys.readouterr().out)
    assert ingress["ingest"][0]["created"] is True

    assert nano_openclaw_main.main([
        "--output", str(tmp_path / "run"),
        "--job", str(job_path),
        "--run-pending",
    ]) == 0
    generated = json.loads(capsys.readouterr().out)
    turn = generated["pending"][0]
    assert turn["status"] == "waiting"
    assert turn["delivery_status"] == "pending"

    assert nano_openclaw_main.main([
        "--output", str(tmp_path / "run"),
        "--job", str(job_path),
        "--list-pending",
    ]) == 0
    inspection = json.loads(capsys.readouterr().out)["inspection"]
    assert inspection["turns"][0]["run_id"] == turn["run_id"]
    assert inspection["outbox"][0]["status"] == "pending"

    assert nano_openclaw_main.main([
        "--output", str(tmp_path / "run"),
        "--job", str(job_path),
        "--delivery-id", turn["run_id"],
    ]) == 0
    delivered = json.loads(capsys.readouterr().out)["delivery"]
    assert delivered["status"] == "completed"
    assert delivered["delivery_status"] == "sent"


def test_cli_duplicate_ingest_reports_reused_identity(tmp_path, capsys):
    job = nano_openclaw_main.GatewayJob.from_file(
        nano_openclaw_main._HERE / "jobs" / "demo.yaml"
    )
    path = tmp_path / "inbound.json"
    path.write_text(json.dumps(job.messages[0].envelope.model_dump(mode="json")))
    arguments = [
        "--output", str(tmp_path / "run"),
        "--ingest", str(path),
    ]

    nano_openclaw_main.main(arguments)
    capsys.readouterr()
    nano_openclaw_main.main(arguments)
    replay = json.loads(capsys.readouterr().out)

    assert replay["ingest"][0]["created"] is False


def test_cli_run_due_queues_schedule_then_delivers_it(tmp_path, capsys):
    output = tmp_path / "run"
    assert nano_openclaw_main.main([
        "--output", str(output),
        "--run-due",
    ]) == 0
    due = json.loads(capsys.readouterr().out)["due"]
    assert due["processed"] == 1
    assert due["turns"][0]["source"] == "schedule"
    assert due["turns"][0]["status"] == "waiting"

    assert nano_openclaw_main.main([
        "--output", str(output),
        "--deliver",
    ]) == 0
    delivery = json.loads(capsys.readouterr().out)["delivery"]
    assert delivery[0]["delivery_status"] == "sent"


def test_ingest_loader_accepts_list_and_rejects_non_objects(tmp_path):
    job = nano_openclaw_main.GatewayJob.from_file(
        nano_openclaw_main._HERE / "jobs" / "demo.yaml"
    )
    valid = tmp_path / "valid.json"
    valid.write_text(json.dumps([
        message.envelope.model_dump(mode="json") for message in job.messages
    ]))
    invalid = tmp_path / "invalid.json"
    invalid.write_text("[1, 2]")

    assert len(nano_openclaw_main._load_envelopes(valid)) == 2
    with pytest.raises(ValueError, match="object"):
        nano_openclaw_main._load_envelopes(invalid)


def test_cli_resume_continues_interrupted_provider_turn(tmp_path, capsys):
    class FailingProvider:
        def chat(self, messages, tools=None):
            raise RuntimeError("provider offline")

    job_path = nano_openclaw_main._HERE / "jobs" / "demo.yaml"
    job = nano_openclaw_main.GatewayJob.from_file(job_path)
    output = tmp_path / "run"
    with nano_openclaw_main.GatewayHost(
        job,
        output,
        provider_factory=lambda _state, _responses: FailingProvider(),
    ) as host:
        interrupted = host.run_job().turns[0]
    assert interrupted.status.value == "interrupted"

    assert nano_openclaw_main.main([
        "--output", str(output),
        "--job", str(job_path),
        "--resume", interrupted.run_id,
    ]) == 0
    resumed = json.loads(capsys.readouterr().out)["resume"]

    assert resumed["run_id"] == interrupted.run_id
    assert resumed["status"] == "waiting"
    assert resumed["delivery_status"] == "pending"
