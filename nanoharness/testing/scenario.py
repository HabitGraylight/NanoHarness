"""Portable deterministic scenarios shared by runnable examples."""

from pathlib import Path, PurePosixPath
from typing import Dict, List

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

from nanoharness.core.schema import ToolCall


SCENARIO_PROTOCOL_VERSION = "1.0"


class ScriptedResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str = ""
    tool_calls: List[ToolCall] = Field(default_factory=list)


class ScenarioExpectation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    success: bool = True
    min_steps: int = Field(default=1, ge=1)
    required_tools: List[str] = Field(default_factory=list)


class Scenario(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = SCENARIO_PROTOCOL_VERSION
    name: str = Field(min_length=1, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    description: str = ""
    query: str = Field(min_length=1)
    fixture_files: Dict[str, str] = Field(default_factory=dict)
    responses: List[ScriptedResponse] = Field(min_length=1)
    expect: ScenarioExpectation = Field(default_factory=ScenarioExpectation)

    @field_validator("schema_version")
    @classmethod
    def supported_version(cls, value: str) -> str:
        if value != SCENARIO_PROTOCOL_VERSION:
            raise ValueError(
                f"unsupported scenario protocol version {value!r}; "
                f"expected {SCENARIO_PROTOCOL_VERSION!r}"
            )
        return value

    @field_validator("fixture_files")
    @classmethod
    def safe_fixture_paths(cls, value: Dict[str, str]) -> Dict[str, str]:
        for raw_path in value:
            path = PurePosixPath(raw_path.replace("\\", "/"))
            if path.is_absolute() or ".." in path.parts or not path.parts:
                raise ValueError(f"fixture path must stay inside the workspace: {raw_path!r}")
        return value

    def materialize(self, workspace: Path) -> None:
        root = workspace.resolve()
        root.mkdir(parents=True, exist_ok=True)
        for relative, content in self.fixture_files.items():
            target = (root / PurePosixPath(relative)).resolve()
            if target != root and root not in target.parents:
                raise ValueError(f"fixture path escapes workspace: {relative!r}")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")


def load_scenario(path: str | Path) -> Scenario:
    source = Path(path)
    payload = yaml.safe_load(source.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Scenario file must contain a YAML object")
    return Scenario.model_validate(payload)
