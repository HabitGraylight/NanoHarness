import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from nanoharness.components.tools import DictToolRegistry
from nanoharness.extensions import ExtensionContext
from nanoharness.profiles import (
    AssemblyPlan,
    AssemblyPlanError,
    HarnessBuilder,
    StagedAssembler,
    build_profile_matrix,
    load_harness_spec,
    load_trace,
)
from nanoharness.testing import Scenario, bind_profile_paths, load_scenario


ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"
EXAMPLE_NAMES = (
    "nano_claude_code",
    "nano_codex",
    "nano_hermes",
    "nano_openclaw",
)
RUNNABLE_EXAMPLE_NAMES = {*EXAMPLE_NAMES, "nano_loop"}
PROFILE_PATHS = [EXAMPLES / name / "profile.yaml" for name in EXAMPLE_NAMES]
SMOKE_ENTRYPOINTS = {
    "nano_claude_code": EXAMPLES / "nano_claude_code" / "profile_demo.py",
    "nano_codex": EXAMPLES / "nano_codex" / "main.py",
    "nano_hermes": EXAMPLES / "nano_hermes" / "main.py",
    "nano_openclaw": EXAMPLES / "nano_openclaw" / "main.py",
}


def test_independent_profiles_are_distinct_and_valid():
    example_directories = {
        child.name
        for child in EXAMPLES.iterdir()
        if child.is_dir() and not child.name.startswith((".", "__"))
    }
    assert example_directories == RUNNABLE_EXAMPLE_NAMES
    for name in RUNNABLE_EXAMPLE_NAMES:
        example = EXAMPLES / name
        assert (example / "main.py").is_file()
        assert (example / "README.md").is_file()
        assert any((example / "tests").rglob("test_*.py"))

    specs = [load_harness_spec(str(path)) for path in PROFILE_PATHS]
    builder = HarnessBuilder()
    matrix = build_profile_matrix(specs, builder=builder)
    rows = {(row.category, row.item): row.values for row in matrix.rows}

    assert [spec.name for spec in specs] == [
        "nano-claude-code",
        "nano-codex",
        "nano-hermes",
        "nano-openclaw",
    ]
    assert all(builder.validate(spec).valid for spec in specs)
    assert rows[("Policy", "tool policy")] == {
        "nano-claude-code": "policy.interactive",
        "nano-codex": "policy.controlled",
        "nano-hermes": "policy.hermes",
        "nano-openclaw": "policy.gateway",
    }
    assert rows[("Capability", "surface.repl")]["nano-claude-code"] == "host"
    assert rows[("Capability", "surface.task")]["nano-codex"] == "host"
    assert rows[("Capability", "learning.reflection")]["nano-hermes"] == "host"
    assert rows[("Capability", "surface.gateway")]["nano-openclaw"] == "host"


@pytest.mark.parametrize("example_name", EXAMPLE_NAMES)
def test_every_example_owns_a_runnable_entrypoint_profile_scenario_and_docs(
    example_name,
    tmp_path,
):
    example = EXAMPLES / example_name
    assert (example / "profile.yaml").is_file()
    assert (example / "scenarios" / "smoke.yaml").is_file()
    assert any((example / "tests").rglob("test_*.py"))
    assert (example / "README.md").is_file()
    result = subprocess.run(
        [
            sys.executable,
            str(SMOKE_ENTRYPOINTS[example_name]),
            "--output",
            str(tmp_path / example_name),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["success"] is True
    if example_name == "nano_openclaw":
        traces = [
            load_trace(turn["artifact"]["trace_path"])
            for turn in payload["turns"]
        ]
        assert payload["delivered"] == 3
        assert payload["processed"] == 3
        assert [turn["tools"] for turn in payload["turns"]] == [
            ["response_submit", "workspace_read"],
            ["response_submit"],
            ["response_submit"],
        ]
        assert [trace.tool_counts for trace in traces] == [
            {"response_submit": 1, "workspace_read": 1},
            {"response_submit": 1},
            {"response_submit": 1},
        ]
    elif example_name == "nano_codex":
        trace = load_trace(payload["artifact"]["trace_path"])
        assert payload["total_steps"] == 6
        assert payload["tools"] == [
            "delivery_submit",
            "execution_finish",
            "plan_submit",
            "review_submit",
            "workspace_read",
            "workspace_search",
            "workspace_test",
            "workspace_write",
        ]
        assert trace.tool_counts == {
            "delivery_submit": 1,
            "review_submit": 1,
            "workspace_read": 1,
        }
    elif example_name == "nano_hermes":
        trace = load_trace(payload["artifact"]["trace_path"])
        assert payload["total_steps"] == 4
        assert payload["tools"] == [
            "assist_submit",
            "memory_propose",
            "reflection_submit",
            "schedule_create",
            "skill_propose",
            "workspace_read",
        ]
        assert trace.tool_counts == {
            "memory_propose": 1,
            "reflection_submit": 1,
            "skill_propose": 1,
        }
    else:
        trace = load_trace(payload["artifact"]["trace_path"])
        assert payload["total_steps"] == 2
        assert payload["tools"] == ["workspace_read"]
        assert trace.tool_counts == {"workspace_read": 1}


def test_scenario_rejects_fixture_path_traversal():
    with pytest.raises(ValidationError, match="inside the workspace"):
        Scenario.model_validate({
            "name": "unsafe",
            "query": "unsafe fixture",
            "fixture_files": {"../escape.txt": "no"},
            "responses": [{"content": "done"}],
        })


def test_each_example_scenario_uses_the_shared_protocol():
    for name in EXAMPLE_NAMES:
        scenario = load_scenario(EXAMPLES / name / "scenarios" / "smoke.yaml")
        assert scenario.schema_version == "1.0"
        assert scenario.expect.required_tools == ["workspace_read"]


def test_policies_live_with_the_examples_and_are_materially_different():
    codex = _load_module("nano_codex_policy", EXAMPLES / "nano_codex" / "app" / "policy.py")
    hermes = _load_module("nano_hermes_policy", EXAMPLES / "nano_hermes" / "app" / "policy.py")
    openclaw = _load_module(
        "nano_openclaw_policy", EXAMPLES / "nano_openclaw" / "app" / "policy.py"
    )
    from nanoharness.core.schema import PolicyOutcome, PolicyStage, ToolRequest

    identity = dict(
        call_id="call_policy",
        run_id="run_policy",
        session_id="session_policy",
        step_id=0,
        arguments={},
    )
    write = ToolRequest(name="workspace_write", **identity)
    skill = ToolRequest(name="skill_propose", **identity)
    save_memory = ToolRequest(name="save_memory", **identity)
    schedule = ToolRequest(name="schedule_create", **identity)
    assert codex.CodexPolicy().decide(
        PolicyStage.BEFORE_TOOL, write
    ).outcome == PolicyOutcome.REQUIRE_APPROVAL
    assert hermes.HermesPolicy().decide(
        PolicyStage.BEFORE_TOOL, skill
    ).outcome == PolicyOutcome.ALLOW
    assert hermes.HermesPolicy().decide(
        PolicyStage.BEFORE_TOOL, save_memory
    ).outcome == PolicyOutcome.DENY
    assert hermes.HermesPolicy().decide(
        PolicyStage.BEFORE_TOOL, schedule
    ).outcome == PolicyOutcome.REQUIRE_APPROVAL
    assert openclaw.GatewayPolicy().decide(
        PolicyStage.BEFORE_TOOL, write
    ).outcome == PolicyOutcome.DENY


def test_nano_claude_profile_declares_complete_staged_assembly():
    spec = load_harness_spec(str(EXAMPLES / "nano_claude_code" / "profile.yaml"))
    plan = AssemblyPlan.from_spec(spec)

    plan.validate(spec)
    assert "memory.file" in plan.bootstrap
    assert "teams.runtime" in plan.bootstrap
    assert plan.runtime == ["subagents.delegate"]


def test_staged_assembler_installs_bootstrap_then_host_then_runtime(tmp_path):
    spec = load_harness_spec(str(EXAMPLES / "nano_claude_code" / "profile.yaml"))
    bound = bind_profile_paths(spec, {
        "workspace": str(tmp_path / "workspace"),
        "runtime": str(tmp_path / "runtime"),
        "skills": str(EXAMPLES / "nano_claude_code" / "skills"),
    })
    context = ExtensionContext(
        tools=DictToolRegistry(),
        capabilities={
            "runtime.llm",
            "tools.workspace",
            "surface.repl",
            "steering.interactive",
        },
        services={"llm.claude": object()},
        metadata={"workspace_root": str(tmp_path / "workspace")},
    )

    def bind_host(active, manager):
        assert "memory.file" in manager.installations
        assert "subagents.delegate" not in manager.installations
        for service in bound.host.services:
            active.services.setdefault(service, object())
        active.capabilities.update(bound.host.capabilities)

    assembly = StagedAssembler().assemble(bound, context, bind_host)
    try:
        assert assembly.phases["runtime"] == ["subagents.delegate"]
        assert "subagents.delegate" in assembly.manager.installations
    finally:
        assembly.close()


def test_assembly_plan_rejects_unassigned_extensions():
    spec = load_harness_spec(str(EXAMPLES / "nano_claude_code" / "profile.yaml"))
    with pytest.raises(AssemblyPlanError, match="missing"):
        AssemblyPlan(bootstrap=["memory.file"], runtime=[]).validate(spec)


def _load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module
