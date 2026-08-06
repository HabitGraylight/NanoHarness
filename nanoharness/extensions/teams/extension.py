"""Managed long-lived teammate extension."""

from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field

from nanoharness.extensions.base import (
    BaseExtension,
    ExtensionContext,
    ExtensionInstallation,
    ExtensionManifest,
)
from nanoharness.extensions.teams.manager import TeammateManager, register_team_tools


class TeamExtensionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_root: Optional[str] = None
    team_dir: Optional[str] = None
    llm_service_name: str = "llm.raw"
    task_service_name: str = "tasks"
    service_name: str = "team"
    tool_prefix: str = ""
    max_turns: int = Field(default=8, ge=1, le=100)
    check_interval: float = Field(default=5.0, gt=0, le=3600)
    idle_max_checks: int = Field(default=12, ge=1, le=10000)
    idle_check_interval: float = Field(default=5.0, gt=0, le=3600)
    shutdown_timeout: float = Field(default=5.0, gt=0, le=60)
    tool_whitelist: List[str] = Field(default_factory=lambda: [
        "file_read", "file_list", "file_find", "search_code", "list_files",
    ])


class TeamExtension(BaseExtension):
    manifest = ExtensionManifest(
        name="teams.runtime",
        version="1.0.0",
        description="Long-lived teammates with inboxes, requests, and notifications.",
        provides=[
            "team.manager",
            "notifications.team",
            "notifications.source",
            "tools.team",
        ],
        requires=["runtime.llm", "tasks.board"],
    )
    config_model = TeamExtensionConfig

    def install(self, context: ExtensionContext, config: BaseModel) -> ExtensionInstallation:
        if not isinstance(config, TeamExtensionConfig):
            raise TypeError("TeamExtension requires TeamExtensionConfig")
        expected = {
            f"{config.tool_prefix}{name}" if config.tool_prefix else name
            for name in (
                "team_spawn",
                "team_send",
                "team_list",
                "team_shutdown",
                "team_request_shutdown",
                "team_submit_plan",
                "team_review",
                "team_requests",
            )
        }
        conflicts = sorted(expected & context.tool_names())
        if conflicts:
            raise ValueError(f"TeamExtension tool conflicts: {conflicts}")
        if config.service_name in context.services:
            raise ValueError(
                f"TeamExtension service conflicts: {config.service_name!r}"
            )
        try:
            llm_client = context.services[config.llm_service_name]
        except KeyError as error:
            raise ValueError(
                f"TeamExtension could not resolve LLM service {config.llm_service_name!r}"
            ) from error
        try:
            task_board = context.services[config.task_service_name]
        except KeyError as error:
            raise ValueError(
                "TeamExtension could not resolve Task Board service "
                f"{config.task_service_name!r}"
            ) from error

        workspace_root = config.workspace_root or context.metadata.get("workspace_root")
        if not workspace_root:
            raise ValueError(
                "TeamExtension requires workspace_root in config or context metadata"
            )
        manager = TeammateManager(
            llm_client=llm_client,
            registry=context.tools,
            workspace_root=workspace_root,
            team_dir=config.team_dir,
            task_board=task_board,
            max_turns=config.max_turns,
            check_interval=config.check_interval,
            idle_max_checks=config.idle_max_checks,
            idle_check_interval=config.idle_check_interval,
            shutdown_timeout=config.shutdown_timeout,
            tool_whitelist=config.tool_whitelist,
        )
        tools = register_team_tools(
            context,
            manager,
            tool_prefix=config.tool_prefix,
        )
        context.provide_service(config.service_name, manager)
        return ExtensionInstallation(
            name=self.manifest.name,
            version=self.manifest.version,
            capabilities=list(self.manifest.provides),
            tools=tools,
            services=[config.service_name],
            config=config.model_dump(mode="json"),
            metadata={
                "workspace_root": manager._workspace_root,
                "team_dir": manager._team_dir,
                "llm_service": config.llm_service_name,
                "task_service": config.task_service_name,
                "notification_source": True,
                "lifecycle": "long-lived",
            },
        )

    def close(
        self,
        context: ExtensionContext,
        installation: ExtensionInstallation,
    ) -> None:
        for service_name in installation.services:
            service = context.services.get(service_name)
            if isinstance(service, TeammateManager):
                service.close()
