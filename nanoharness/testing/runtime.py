"""Neutral deterministic runtime used by independently runnable examples."""

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from pydantic import BaseModel, ConfigDict, Field

from nanoharness.components.context import SimpleContextManager
from nanoharness.components.evaluator import TraceEvaluator
from nanoharness.components.hooks import SimpleHookManager
from nanoharness.components.lifecycle import (
    CallbackApprovalBroker,
    JsonlEventSink,
    RegistryToolExecutor,
)
from nanoharness.components.state import JsonStateStore
from nanoharness.components.tools import DictToolRegistry
from nanoharness.extensions import ExtensionContext
from nanoharness.profiles import HarnessBuild, HarnessBuilder, HarnessSpec
from nanoharness.testing.artifacts import ArtifactRecord, RunArtifactStore
from nanoharness.testing.scenario import Scenario, load_scenario
from nanoharness.testing.scripted import ScriptedLLM


ToolContributor = Callable[[DictToolRegistry, Path], None]
_PLACEHOLDER = re.compile(r"\$\{([^}]+)\}")


class ScenarioRunResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile: str
    scenario: str
    status: str
    success: bool
    total_steps: int
    tools: list[str] = Field(default_factory=list)
    artifact: ArtifactRecord


@dataclass
class ScenarioHarness:
    build: HarnessBuild
    workspace: Path
    runtime_root: Path

    @property
    def engine(self):
        if self.build.engine is None:
            raise RuntimeError("Scenario profile did not bind a NanoEngine")
        return self.build.engine

    def close(self) -> None:
        self.build.close()


def bind_profile_paths(spec: HarnessSpec, bindings: Dict[str, str]) -> HarnessSpec:
    """Resolve explicit example placeholders without reading environment vars."""

    def bind(value):
        if isinstance(value, dict):
            return {key: bind(item) for key, item in value.items()}
        if isinstance(value, list):
            return [bind(item) for item in value]
        if not isinstance(value, str):
            return value

        def replace(match: re.Match) -> str:
            name = match.group(1)
            if name not in bindings:
                raise ValueError(f"Unknown example placeholder: {name!r}")
            return bindings[name]

        return _PLACEHOLDER.sub(replace, value)

    return HarnessSpec.model_validate(bind(spec.model_dump(mode="json")))


def build_scenario_harness(
    spec: HarnessSpec,
    scenario: Scenario,
    *,
    workspace: str | Path,
    runtime_root: str | Path,
    skills_root: str | Path,
    policy,
    contribute_tools: Optional[ToolContributor] = None,
    builder: Optional[HarnessBuilder] = None,
) -> ScenarioHarness:
    workspace_path = Path(workspace).resolve()
    runtime_path = Path(runtime_root).resolve()
    skills_path = Path(skills_root).resolve()
    workspace_path.mkdir(parents=True, exist_ok=True)
    runtime_path.mkdir(parents=True, exist_ok=True)
    scenario.materialize(workspace_path)
    bound = bind_profile_paths(spec, {
        "workspace": str(workspace_path),
        "runtime": str(runtime_path),
        "skills": str(skills_path),
    })
    tools = _workspace_tools(workspace_path)
    if contribute_tools is not None:
        contribute_tools(tools, workspace_path)
    provider = ScriptedLLM(scenario.responses)
    binding = bound.engine
    if binding is None:
        raise ValueError("Runnable example profiles must declare an engine")
    services: Dict[str, Any] = {
        binding.llm_service: provider,
        binding.context_service: SimpleContextManager(
            system_prompt=f"Deterministic smoke host for {bound.name}."
        ),
        binding.state_service: JsonStateStore(str(runtime_path / "state.json")),
        binding.hooks_service: SimpleHookManager(),
        binding.evaluator_service: TraceEvaluator(),
    }
    if binding.policy_service:
        services[binding.policy_service] = policy
    if binding.approval_broker_service:
        services[binding.approval_broker_service] = CallbackApprovalBroker(
            lambda request, decision: True
        )
    if binding.executor_service:
        services[binding.executor_service] = RegistryToolExecutor(tools)
    if binding.event_sink_service:
        services[binding.event_sink_service] = JsonlEventSink(
            str(runtime_path / "events.jsonl")
        )
    context = ExtensionContext(
        tools=tools,
        services=services,
        capabilities=set(bound.host.capabilities),
        metadata={"workspace_root": str(workspace_path)},
    )
    build = (builder or HarnessBuilder()).build(bound, context=context)
    return ScenarioHarness(build, workspace_path, runtime_path)


def run_profile_scenario(
    *,
    profile_path: str | Path,
    scenario_path: str | Path,
    workspace: str | Path,
    runtime_root: str | Path,
    artifact_root: str | Path,
    skills_root: str | Path,
    policy,
    contribute_tools: Optional[ToolContributor] = None,
) -> ScenarioRunResult:
    spec = HarnessSpec.from_file(str(profile_path))
    scenario = load_scenario(scenario_path)
    harness = build_scenario_harness(
        spec,
        scenario,
        workspace=workspace,
        runtime_root=runtime_root,
        skills_root=skills_root,
        policy=policy,
        contribute_tools=contribute_tools,
    )
    try:
        report = harness.engine.run(scenario.query)
        artifact, trace = RunArtifactStore(artifact_root).save(
            profile=spec.name,
            scenario=scenario.name,
            report=report,
        )
        _assert_expectations(scenario, trace)
        return ScenarioRunResult(
            profile=spec.name,
            scenario=scenario.name,
            status=trace.status,
            success=bool(trace.success),
            total_steps=trace.total_steps,
            tools=sorted(trace.tool_counts),
            artifact=artifact,
        )
    finally:
        harness.close()


def _assert_expectations(scenario, trace) -> None:
    expected = scenario.expect
    if trace.success is not expected.success:
        raise AssertionError(
            f"Expected success={expected.success}, got {trace.success!r}"
        )
    if trace.total_steps < expected.min_steps:
        raise AssertionError(
            f"Expected at least {expected.min_steps} steps, got {trace.total_steps}"
        )
    missing = sorted(set(expected.required_tools) - set(trace.tool_counts))
    if missing:
        raise AssertionError(f"Required tools were not called: {missing}")


def _workspace_tools(workspace: Path) -> DictToolRegistry:
    tools = DictToolRegistry()

    def resolve(relative: str) -> Path:
        target = (workspace / relative).resolve()
        if target != workspace and workspace not in target.parents:
            raise ValueError(f"Path escapes example workspace: {relative!r}")
        return target

    @tools.tool
    def workspace_read(path: str) -> str:
        """Read one UTF-8 file inside the isolated example workspace."""
        return resolve(path).read_text(encoding="utf-8")

    @tools.tool
    def workspace_write(path: str, content: str) -> str:
        """Write one UTF-8 file inside the isolated example workspace."""
        target = resolve(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return f"wrote {path}"

    return tools
