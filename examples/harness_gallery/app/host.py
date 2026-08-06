"""Host bindings that turn Gallery HarnessSpecs into runnable NanoEngines."""

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict

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
from nanoharness.core.schema import (
    PolicyDecision,
    PolicyOutcome,
    PolicyStage,
    ToolRequest,
)
from nanoharness.extensions import ExtensionContext
from nanoharness.profiles import HarnessBuild, HarnessBuilder, HarnessSpec

from app.provider import ScriptedLLM
from app.schema import Scenario


_PLACEHOLDER = re.compile(r"\$\{([^}]+)\}")


class GalleryPolicy:
    """Small real policy differences used by the three reference profiles."""

    def __init__(self, mode: str):
        if mode not in {"interactive", "controlled", "gateway", "hermes"}:
            raise ValueError(f"Unknown Gallery policy mode: {mode!r}")
        self.mode = mode

    def decide(self, stage, request, execution=None) -> PolicyDecision:
        if stage == PolicyStage.AFTER_TOOL:
            return PolicyDecision(
                outcome=PolicyOutcome.ALLOW,
                source=f"gallery.{self.mode}",
            )
        if self.mode == "gateway" and request.name == "workspace_write":
            return PolicyDecision(
                outcome=PolicyOutcome.DENY,
                reason="Gateway profiles cannot mutate the workspace",
                source="gallery.gateway",
            )
        if self.mode == "controlled" and request.name == "channel_send":
            return PolicyDecision(
                outcome=PolicyOutcome.DENY,
                reason="Controlled coding runs cannot send channel messages",
                source="gallery.controlled",
            )
        if self.mode == "hermes" and request.name in {"skill_propose", "channel_send"}:
            return PolicyDecision(
                outcome=PolicyOutcome.REQUIRE_APPROVAL,
                reason="Hermes learning and external delivery changes are staged for review",
                source="gallery.hermes",
            )
        if request.name == "workspace_write":
            return PolicyDecision(
                outcome=PolicyOutcome.REQUIRE_APPROVAL,
                reason=f"{self.mode} profile requires approval for workspace writes",
                source=f"gallery.{self.mode}",
            )
        return PolicyDecision(
            outcome=PolicyOutcome.ALLOW,
            source=f"gallery.{self.mode}",
        )


@dataclass
class GalleryHarness:
    build: HarnessBuild
    workspace: Path
    runtime_root: Path

    @property
    def engine(self):
        if self.build.engine is None:
            raise RuntimeError("Gallery profile did not bind a NanoEngine")
        return self.build.engine

    def close(self) -> None:
        self.build.close()


class GalleryHost:
    """Bind serializable Profile names to deterministic local runtime objects."""

    def __init__(self, builder: HarnessBuilder | None = None):
        self.builder = builder or HarnessBuilder()

    def build(
        self,
        spec: HarnessSpec,
        scenario: Scenario,
        *,
        workspace: str | Path,
        runtime_root: str | Path,
        skills_root: str | Path,
    ) -> GalleryHarness:
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
        policy_mode = str(bound.metadata.get("policy_mode") or "interactive")
        tools = _workspace_tools(workspace_path, policy_mode=policy_mode)
        provider = ScriptedLLM(scenario.responses)
        binding = bound.engine
        if binding is None:
            raise ValueError("Gallery profiles must declare an engine binding")
        services: Dict[str, Any] = {
            binding.llm_service: provider,
            binding.context_service: SimpleContextManager(
                system_prompt=(
                    f"Gallery profile {bound.name}. Execute the scripted scenario "
                    "using only the provided tools."
                )
            ),
            binding.state_service: JsonStateStore(str(runtime_path / "state.json")),
            binding.hooks_service: SimpleHookManager(),
            binding.evaluator_service: TraceEvaluator(),
        }
        if binding.policy_service:
            services[binding.policy_service] = GalleryPolicy(policy_mode)
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
            metadata={
                "workspace_root": str(workspace_path),
                "gallery_profile": bound.name,
            },
        )
        build = self.builder.build(bound, context=context)
        return GalleryHarness(
            build=build,
            workspace=workspace_path,
            runtime_root=runtime_path,
        )


def bind_profile_paths(spec: HarnessSpec, bindings: Dict[str, str]) -> HarnessSpec:
    """Resolve only explicit Gallery placeholders; never expand environment vars."""

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
                raise ValueError(f"Unknown Gallery placeholder: {name!r}")
            return bindings[name]

        return _PLACEHOLDER.sub(replace, value)

    return HarnessSpec.model_validate(bind(spec.model_dump(mode="json")))


def _workspace_tools(workspace: Path, *, policy_mode: str) -> DictToolRegistry:
    tools = DictToolRegistry()

    def resolve(relative: str) -> Path:
        target = (workspace / relative).resolve()
        if target != workspace and workspace not in target.parents:
            raise ValueError(f"Path escapes Gallery workspace: {relative!r}")
        return target

    @tools.tool
    def workspace_read(path: str) -> str:
        """Read one UTF-8 file inside the isolated Gallery workspace."""
        return resolve(path).read_text(encoding="utf-8")

    @tools.tool
    def workspace_write(path: str, content: str) -> str:
        """Write one UTF-8 file inside the isolated Gallery workspace."""
        target = resolve(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return f"wrote {path}"

    if policy_mode in {"gateway", "hermes"}:
        @tools.tool
        def channel_send(channel: str, content: str) -> str:
            """Emit a deterministic mock channel delivery acknowledgement."""
            return f"delivered to {channel}: {len(content)} chars"

    if policy_mode == "hermes":
        @tools.tool
        def skill_propose(name: str, content: str) -> str:
            """Stage a learned skill for review without changing the live catalog."""
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", name):
                raise ValueError("Skill name contains unsupported characters")
            target = resolve(f".gallery/pending_skills/{name}.md")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            return f"staged skill proposal {name}"

    return tools
