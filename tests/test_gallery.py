import json
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError


GALLERY_ROOT = Path(__file__).resolve().parents[1] / "examples" / "harness_gallery"
if str(GALLERY_ROOT) not in sys.path:
    sys.path.insert(0, str(GALLERY_ROOT))

from app.host import GalleryPolicy, bind_profile_paths
from app.runner import GalleryRunner
from app.schema import Scenario, load_scenario
from nanoharness.core.schema import PolicyOutcome, PolicyStage, ToolRequest
from nanoharness.profiles import (
    HarnessBuilder,
    build_profile_matrix,
    load_harness_spec,
    load_trace,
)


PROFILE_PATHS = sorted((GALLERY_ROOT / "profiles").glob("*.yaml"))
SCENARIO_PATH = GALLERY_ROOT / "scenarios" / "inspect_workspace.yaml"


def test_gallery_profiles_are_distinct_valid_harnesses():
    specs = [load_harness_spec(str(path)) for path in PROFILE_PATHS]
    builder = HarnessBuilder()

    validations = [builder.validate(spec) for spec in specs]
    matrix = build_profile_matrix(specs, builder=builder)
    rows = {(row.category, row.item): row.values for row in matrix.rows}

    assert [spec.name for spec in specs] == [
        "nano-claude-code",
        "nano-codex",
        "nano-hermes",
        "nano-openclaw",
    ]
    assert all(item.valid for item in validations)
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
    assert rows[("Extension", "worktrees.git")]["nano-openclaw"] == "—"
    assert rows[("Extension", "scheduler.local")]["nano-codex"] == "—"


def test_scenario_rejects_fixture_path_traversal():
    with pytest.raises(ValidationError, match="inside the workspace"):
        Scenario.model_validate({
            "name": "unsafe",
            "query": "unsafe fixture",
            "fixture_files": {"../escape.txt": "no"},
            "responses": [{"content": "done"}],
        })


def test_profile_binding_resolves_only_explicit_placeholders(tmp_path):
    spec = load_harness_spec(str(PROFILE_PATHS[0]))
    bound = bind_profile_paths(spec, {
        "workspace": str(tmp_path / "workspace"),
        "runtime": str(tmp_path / "runtime"),
        "skills": str(GALLERY_ROOT / "skills"),
    })
    dumped = bound.model_dump_json()

    assert "${" not in dumped
    assert str(tmp_path / "workspace") in dumped


def test_gallery_policies_have_materially_different_write_and_channel_rules():
    identity = {
        "call_id": "call_policy",
        "run_id": "run_policy",
        "session_id": "session_policy",
        "step_id": 0,
    }
    write = ToolRequest(name="workspace_write", arguments={}, **identity)
    channel = ToolRequest(name="channel_send", arguments={}, **identity)

    assert GalleryPolicy("interactive").decide(
        PolicyStage.BEFORE_TOOL, write
    ).outcome == PolicyOutcome.REQUIRE_APPROVAL
    assert GalleryPolicy("controlled").decide(
        PolicyStage.BEFORE_TOOL, channel
    ).outcome == PolicyOutcome.DENY
    assert GalleryPolicy("gateway").decide(
        PolicyStage.BEFORE_TOOL, write
    ).outcome == PolicyOutcome.DENY
    assert GalleryPolicy("gateway").decide(
        PolicyStage.BEFORE_TOOL, channel
    ).outcome == PolicyOutcome.ALLOW
    skill = ToolRequest(name="skill_propose", arguments={}, **identity)
    assert GalleryPolicy("hermes").decide(
        PolicyStage.BEFORE_TOOL, skill
    ).outcome == PolicyOutcome.REQUIRE_APPROVAL


@pytest.mark.parametrize("profile_path", PROFILE_PATHS, ids=lambda path: path.stem)
def test_every_gallery_profile_builds_and_runs_the_same_scenario(
    profile_path,
    tmp_path,
):
    profile_name = load_harness_spec(str(profile_path)).name
    result = GalleryRunner().run(
        profile_path=profile_path,
        scenario_path=SCENARIO_PATH,
        workspace=tmp_path / "workspace" / profile_name,
        runtime_root=tmp_path / "runtime" / profile_name,
        artifact_root=tmp_path / "artifacts",
        skills_root=GALLERY_ROOT / "skills",
    )
    report_path = Path(result.artifact.report_path)
    trace_path = Path(result.artifact.trace_path)
    report_text = report_path.read_text(encoding="utf-8")
    trace_text = trace_path.read_text(encoding="utf-8")
    trace = load_trace(str(trace_path))

    assert result.profile == profile_name
    assert result.success is True
    assert result.total_steps == 2
    assert result.tools == ["workspace_read"]
    assert report_path.exists()
    assert json.loads(report_text)["run"]["status"] == "completed"
    assert "one composable kernel" in report_text.lower()
    assert "one composable kernel" not in trace_text.lower()
    assert trace.source_kind == "report"
    assert trace.tool_counts == {"workspace_read": 1}


def test_shared_scenario_is_provider_neutral_and_versioned():
    scenario = load_scenario(SCENARIO_PATH)

    assert scenario.schema_version == "1.0"
    assert scenario.expect.required_tools == ["workspace_read"]
    assert scenario.responses[0].tool_calls[0].call_id == "gallery_read_1"
