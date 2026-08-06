"""Typed declarations and reports for composable harness profiles."""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


HARNESS_SPEC_VERSION = "1.0"


def _unique(values: List[str]) -> List[str]:
    seen = set()
    result = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


class HostRequirements(BaseModel):
    """Runtime bindings a host must supply before this profile can build."""

    model_config = ConfigDict(extra="forbid")

    capabilities: List[str] = Field(default_factory=list)
    services: List[str] = Field(default_factory=list)

    @field_validator("capabilities", "services")
    @classmethod
    def unique_names(cls, values: List[str]) -> List[str]:
        return _unique(values)


class ExtensionSpec(BaseModel):
    """One requested extension and its public configuration."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    enabled: bool = True
    config: Dict[str, Any] = Field(default_factory=dict)


class EngineSpec(BaseModel):
    """Service bindings and scalar settings for the ETCSLV engine."""

    model_config = ConfigDict(extra="forbid")

    llm_service: str = "llm"
    context_service: str = "context"
    state_service: str = "state"
    hooks_service: str = "hooks"
    evaluator_service: str = "evaluator"
    policy_service: Optional[str] = None
    approval_broker_service: Optional[str] = None
    executor_service: Optional[str] = None
    event_sink_service: Optional[str] = None
    max_steps: int = Field(default=10, ge=1, le=100000)
    session_id: Optional[str] = None

    def service_names(self) -> List[str]:
        return [
            name
            for name in (
                self.llm_service,
                self.context_service,
                self.state_service,
                self.hooks_service,
                self.evaluator_service,
                self.policy_service,
                self.approval_broker_service,
                self.executor_service,
                self.event_sink_service,
            )
            if name is not None
        ]


class HarnessSpec(BaseModel):
    """Serializable recipe for a capability-composed harness."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = HARNESS_SPEC_VERSION
    name: str = Field(min_length=1, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    description: str = ""
    host: HostRequirements = Field(default_factory=HostRequirements)
    engine: Optional[EngineSpec] = None
    extensions: List[ExtensionSpec] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def from_file(cls, path: str) -> "HarnessSpec":
        from nanoharness.profiles.io import load_harness_spec

        return load_harness_spec(path)


class HarnessIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    message: str
    extension: Optional[str] = None
    capability: Optional[str] = None
    field: Optional[str] = None


class DependencyEdge(BaseModel):
    model_config = ConfigDict(extra="forbid")

    extension: str
    capability: str
    providers: List[str] = Field(default_factory=list)


class HarnessValidation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    spec_name: str
    valid: bool
    errors: List[HarnessIssue] = Field(default_factory=list)
    warnings: List[HarnessIssue] = Field(default_factory=list)
    installation_order: List[str] = Field(default_factory=list)
    capabilities: List[str] = Field(default_factory=list)
    dependencies: List[DependencyEdge] = Field(default_factory=list)


class PlannedExtension(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    enabled: bool = True
    version: str
    description: str = ""
    requested_index: int
    installation_index: Optional[int] = None
    provides: List[str] = Field(default_factory=list)
    requires: List[str] = Field(default_factory=list)
    conflicts: List[str] = Field(default_factory=list)
    config: Dict[str, Any] = Field(default_factory=dict)
    config_schema: Dict[str, Any] = Field(default_factory=dict)


class HarnessExplanation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str
    name: str
    description: str = ""
    valid: bool
    host: HostRequirements
    engine: Optional[EngineSpec] = None
    installation_order: List[str] = Field(default_factory=list)
    extensions: List[PlannedExtension] = Field(default_factory=list)
    capabilities: Dict[str, List[str]] = Field(default_factory=dict)
    dependencies: List[DependencyEdge] = Field(default_factory=list)
    errors: List[HarnessIssue] = Field(default_factory=list)
    warnings: List[HarnessIssue] = Field(default_factory=list)
