"""Scenario runner shared by every Harness Gallery profile."""

from pathlib import Path
from typing import List

from pydantic import BaseModel, ConfigDict, Field

from nanoharness.profiles import HarnessBuilder, load_harness_spec

from app.artifacts import ArtifactRecord, RunArtifactStore
from app.host import GalleryHost
from app.schema import Scenario, load_scenario


class GalleryRunResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile: str
    scenario: str
    status: str
    success: bool
    total_steps: int
    tools: List[str] = Field(default_factory=list)
    artifact: ArtifactRecord


class GalleryRunner:
    def __init__(self, builder: HarnessBuilder | None = None):
        self.builder = builder or HarnessBuilder()
        self.host = GalleryHost(self.builder)

    def run(
        self,
        *,
        profile_path: str | Path,
        scenario_path: str | Path,
        workspace: str | Path,
        runtime_root: str | Path,
        artifact_root: str | Path,
        skills_root: str | Path,
    ) -> GalleryRunResult:
        spec = load_harness_spec(str(profile_path))
        scenario = load_scenario(scenario_path)
        harness = self.host.build(
            spec,
            scenario,
            workspace=workspace,
            runtime_root=runtime_root,
            skills_root=skills_root,
        )
        try:
            report = harness.engine.run(scenario.query)
            artifact, trace = RunArtifactStore(artifact_root).save(
                profile=spec.name,
                scenario=scenario.name,
                report=report,
            )
            self._assert_expectations(scenario, trace)
            return GalleryRunResult(
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

    @staticmethod
    def _assert_expectations(scenario: Scenario, trace) -> None:
        expected = scenario.expect
        if trace.success is not expected.success:
            raise AssertionError(
                f"Scenario {scenario.name!r} expected success={expected.success}, "
                f"got {trace.success!r}"
            )
        if trace.total_steps < expected.min_steps:
            raise AssertionError(
                f"Scenario {scenario.name!r} expected at least "
                f"{expected.min_steps} steps, got {trace.total_steps}"
            )
        missing = sorted(set(expected.required_tools) - set(trace.tool_counts))
        if missing:
            raise AssertionError(
                f"Scenario {scenario.name!r} did not call required tools: {missing}"
            )
