"""Schema-first one-shot subagent extension."""

from typing import List

from pydantic import BaseModel, ConfigDict, Field

from nanoharness.extensions.base import (
    BaseExtension,
    ExtensionContext,
    ExtensionInstallation,
    ExtensionManifest,
)
from nanoharness.extensions.subagents.runtime import (
    SUBAGENT_TOOL_WHITELIST,
    SubagentRunner,
)


class SubagentExtensionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    llm_service_name: str = "llm.agent"
    context_service_name: str = "context.agent"
    service_name: str = "subagents"
    tool_name: str = "task"
    max_turns: int = Field(default=8, ge=1, le=100)
    tool_whitelist: List[str] = Field(
        default_factory=lambda: sorted(SUBAGENT_TOOL_WHITELIST)
    )


def register_subagent_tool(
    registry,
    runner: SubagentRunner,
    *,
    tool_name: str = "task",
) -> List[str]:
    def task_handler(args):
        try:
            return runner.delegate(
                args.get("description", ""),
                fork=bool(args.get("fork", False)),
            )
        except Exception as error:
            if isinstance(error, RuntimeError):
                raise
            raise RuntimeError(f"Subagent error: {error}") from error

    registry.register(
        name=tool_name,
        handler=task_handler,
        schema={
            "type": "function",
            "function": {
                "name": tool_name,
                "description": (
                    "Delegate a focused read-only subtask to a one-shot subagent. "
                    "Set fork=true to copy the current conversation into the delegate."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "description": {
                            "type": "string",
                            "description": "Clear, specific subtask description",
                        },
                        "fork": {
                            "type": "boolean",
                            "description": "Whether to inherit the parent conversation",
                        },
                    },
                    "required": ["description"],
                },
            },
        },
    )
    return [tool_name]


class SubagentExtension(BaseExtension):
    manifest = ExtensionManifest(
        name="subagents.delegate",
        version="1.0.0",
        description="One-shot isolated delegation with a read-only tool subset.",
        provides=["subagents.delegate", "tools.subagents"],
        requires=["runtime.agent_llm", "runtime.context"],
    )
    config_model = SubagentExtensionConfig

    def install(self, context: ExtensionContext, config: BaseModel) -> ExtensionInstallation:
        if not isinstance(config, SubagentExtensionConfig):
            raise TypeError("SubagentExtension requires SubagentExtensionConfig")
        if config.tool_name in context.tool_names():
            raise ValueError(
                f"SubagentExtension tool conflicts: {[config.tool_name]}"
            )
        if config.service_name in context.services:
            raise ValueError(
                f"SubagentExtension service conflicts: {config.service_name!r}"
            )
        try:
            llm_client = context.services[config.llm_service_name]
        except KeyError as error:
            raise ValueError(
                "SubagentExtension could not resolve LLM service "
                f"{config.llm_service_name!r}"
            ) from error
        try:
            parent_context = context.services[config.context_service_name]
        except KeyError as error:
            raise ValueError(
                "SubagentExtension could not resolve context service "
                f"{config.context_service_name!r}"
            ) from error

        runner = SubagentRunner(
            registry=context.tools,
            llm_client=llm_client,
            parent_context=parent_context,
            max_turns=config.max_turns,
            tool_whitelist=frozenset(config.tool_whitelist),
        )
        tools = register_subagent_tool(
            context,
            runner,
            tool_name=config.tool_name,
        )
        context.provide_service(config.service_name, runner)
        return ExtensionInstallation(
            name=self.manifest.name,
            version=self.manifest.version,
            capabilities=list(self.manifest.provides),
            tools=tools,
            services=[config.service_name],
            config=config.model_dump(mode="json"),
            metadata={
                "llm_service": config.llm_service_name,
                "context_service": config.context_service_name,
                "tool_whitelist": sorted(runner.tool_whitelist),
                "lifecycle": "one-shot",
            },
        )
