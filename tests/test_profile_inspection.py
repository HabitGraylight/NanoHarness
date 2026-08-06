import json

import pytest

from nanoharness.profiles import (
    HarnessBuilder,
    HarnessSpec,
    build_profile_matrix,
    compare_traces,
    load_trace,
    summarize_trace,
)
from nanoharness.profiles.cli import main as profile_cli


def sample_report(*, tool_calls=1, run_id="run_demo"):
    actions = [
        {
            "call_id": f"call_{index}",
            "name": "file_read",
            "arguments": {"path": f"secret-{index}.txt"},
            "status": "success",
            "output": f"private output {index}",
            "metadata": {},
        }
        for index in range(tool_calls)
    ]
    return {
        "run": {
            "run_id": run_id,
            "session_id": "session_demo",
            "protocol_version": "2.1",
            "status": "completed",
            "stop_reason": "",
        },
        "summary": {
            "success": True,
            "total_steps": 1,
            "evaluation": {
                "achieved": True,
                "confidence": 0.9,
                "explanation": "private evaluator output",
            },
        },
        "trajectory": [{
            "step_id": 0,
            "thought": "private chain of thought",
            "actions": actions,
            "action": None,
            "observation": "private observation",
            "status": "terminated",
            "stop_signal": None,
        }],
        "events": [
            {
                "run_id": run_id,
                "session_id": "session_demo",
                "sequence": 0,
                "type": "run_started",
                "timestamp": "2026-08-06T00:00:00+00:00",
                "data": {},
            },
            {
                "run_id": run_id,
                "session_id": "session_demo",
                "sequence": 1,
                "type": "policy_evaluated",
                "timestamp": "2026-08-06T00:00:01+00:00",
                "step_id": 0,
                "data": {"decision": {"outcome": "allow"}},
            },
            {
                "run_id": run_id,
                "session_id": "session_demo",
                "sequence": 2,
                "type": "run_completed",
                "timestamp": "2026-08-06T00:00:02+00:00",
                "data": {"status": "completed", "success": True},
            },
        ],
    }


def test_report_trace_is_content_minimizing_and_counts_runtime_facts():
    report = sample_report(tool_calls=2)

    trace = summarize_trace(report)
    dumped = trace.model_dump_json()

    assert trace.source_kind == "report"
    assert trace.run_id == "run_demo"
    assert trace.total_steps == 1
    assert trace.total_tool_calls == 2
    assert trace.tool_counts == {"file_read": 2}
    assert trace.tool_status_counts == {"success": 2}
    assert trace.step_status_counts == {"terminated": 1}
    assert trace.policy_outcomes == {"allow": 1}
    assert trace.duration_seconds == 2.0
    assert trace.steps[0].thought_chars == len("private chain of thought")
    assert "private" not in dumped
    assert "secret-0.txt" not in dumped


def test_checkpoint_trace_supports_crash_readable_state():
    checkpoint = {
        "run_id": "run_checkpoint",
        "session_id": "session_checkpoint",
        "protocol_version": "2.1",
        "status": "failed",
        "stop_reason": "provider failed",
        "trajectory": sample_report()["trajectory"],
    }

    trace = summarize_trace(checkpoint)

    assert trace.source_kind == "checkpoint"
    assert trace.status == "failed"
    assert trace.success is None
    assert trace.total_tool_calls == 1


def test_event_jsonl_trace_infers_status_evaluation_and_tool_counts(tmp_path):
    events = sample_report()["events"]
    events.insert(2, {
        "run_id": "run_demo",
        "session_id": "session_demo",
        "sequence": 2,
        "type": "tool_completed",
        "timestamp": "2026-08-06T00:00:01.500000+00:00",
        "step_id": 0,
        "data": {"execution": {"name": "search_code", "status": "success"}},
    })
    events.insert(3, {
        "run_id": "run_demo",
        "session_id": "session_demo",
        "sequence": 3,
        "type": "evaluation_completed",
        "timestamp": "2026-08-06T00:00:01.700000+00:00",
        "data": {"evaluation": {"achieved": True, "confidence": 0.75}},
    })
    path = tmp_path / "events.jsonl"
    path.write_text(
        "\n".join(json.dumps(event) for event in events) + "\n",
        encoding="utf-8",
    )

    trace = load_trace(str(path))

    assert trace.source_kind == "events"
    assert trace.status == "completed"
    assert trace.success is True
    assert trace.achieved is True
    assert trace.confidence == 0.75
    assert trace.tool_counts == {"search_code": 1}
    assert trace.event_counts["evaluation_completed"] == 1


def test_trace_rejects_unrecognized_payload():
    with pytest.raises(ValueError, match="Unrecognized"):
        summarize_trace({"hello": "world"})


def test_normalized_trace_json_round_trips_through_loader(tmp_path):
    expected = summarize_trace(sample_report())
    path = tmp_path / "trace.json"
    path.write_text(expected.model_dump_json(), encoding="utf-8")

    assert load_trace(str(path)) == expected


def test_trace_comparison_reports_metric_and_counter_deltas():
    left = summarize_trace(sample_report(tool_calls=1, run_id="run_left"))
    right = summarize_trace(sample_report(tool_calls=3, run_id="run_right"))

    comparison = compare_traces(
        left,
        right,
        left_label="solo",
        right_label="team",
    )
    metrics = {metric.metric: metric for metric in comparison.metrics}

    assert comparison.left == "solo"
    assert metrics["total_tool_calls"].delta == 2.0
    assert comparison.tool_call_deltas == {"file_read": 2}
    assert comparison.tool_status_deltas == {"success": 2}


def profile_specs():
    solo = HarnessSpec.model_validate({
        "name": "solo",
        "host": {
            "capabilities": ["runtime.agent_llm", "runtime.context"],
            "services": ["llm.agent", "context.agent"],
        },
        "extensions": [{"name": "subagents.delegate"}],
    })
    team = HarnessSpec.model_validate({
        "name": "team",
        "host": {
            "capabilities": ["runtime.llm"],
            "services": ["llm.raw"],
        },
        "engine": {
            "llm_service": "llm.raw",
            "context_service": "context",
            "state_service": "state",
            "hooks_service": "hooks",
            "evaluator_service": "evaluator",
            "policy_service": "policy",
        },
        "extensions": [{"name": "tasks.board"}],
    })
    return solo, team


def test_matrix_generates_etcslv_capability_extension_and_policy_rows():
    solo, team = profile_specs()

    matrix = build_profile_matrix([solo, team])
    rows = {(row.category, row.item): row.values for row in matrix.rows}

    assert matrix.profiles == ["solo", "team"]
    assert rows[("ETCSLV", "E: execution")] == {
        "solo": "unbound",
        "team": "NanoEngine",
    }
    assert rows[("ETCSLV", "C: context")]["team"] == "context"
    assert rows[("Policy", "tool policy")]["team"] == "policy"
    assert rows[("Capability", "subagents.delegate")]["solo"] == (
        "subagents.delegate"
    )
    assert rows[("Extension", "tasks.board")]["team"].startswith("v1.0.0")
    assert rows[("Host Service", "llm.agent")]["solo"] == "required"
    assert matrix.valid["solo"] is True
    assert matrix.valid["team"] is False
    assert "team" in matrix.errors


def test_matrix_disambiguates_duplicate_profile_names():
    solo, _ = profile_specs()

    matrix = build_profile_matrix([solo, solo])

    assert matrix.profiles == ["solo", "solo#2"]


def test_cli_trace_compare_and_matrix(tmp_path, capsys):
    left_path = tmp_path / "left.json"
    right_path = tmp_path / "right.json"
    left_path.write_text(json.dumps(sample_report(tool_calls=1)), encoding="utf-8")
    right_path.write_text(json.dumps(sample_report(tool_calls=2)), encoding="utf-8")

    assert profile_cli(["trace", str(left_path), "--compact"]) == 0
    trace = json.loads(capsys.readouterr().out)
    assert trace["total_tool_calls"] == 1

    assert profile_cli([
        "compare", str(left_path), str(right_path), "--compact"
    ]) == 0
    comparison = json.loads(capsys.readouterr().out)
    assert comparison["tool_call_deltas"] == {"file_read": 1}

    profile_path = tmp_path / "profile.yaml"
    profile_path.write_text(
        "name: matrix-profile\nextensions:\n  - name: tasks.board\n",
        encoding="utf-8",
    )
    assert profile_cli(["matrix", str(profile_path), "--compact"]) == 0
    matrix = json.loads(capsys.readouterr().out)
    assert matrix["profiles"] == ["matrix-profile"]


def test_cli_compare_auto_detects_profiles(tmp_path, capsys):
    left = tmp_path / "left.yaml"
    right = tmp_path / "right.toml"
    left.write_text(
        "name: left-profile\nextensions:\n  - name: tasks.board\n",
        encoding="utf-8",
    )
    right.write_text(
        'name = "right-profile"\n[[extensions]]\nname = "tasks.board"\n',
        encoding="utf-8",
    )

    assert profile_cli([
        "compare", str(left), str(right), "--compact"
    ]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["profiles"] == ["left-profile", "right-profile"]


def test_cli_compare_auto_detects_minimal_json_profiles(tmp_path, capsys):
    left = tmp_path / "left.json"
    right = tmp_path / "right.json"
    left.write_text(json.dumps({"name": "minimal-left"}), encoding="utf-8")
    right.write_text(json.dumps({"name": "minimal-right"}), encoding="utf-8")

    assert profile_cli([
        "compare", str(left), str(right), "--compact"
    ]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["profiles"] == ["minimal-left", "minimal-right"]
