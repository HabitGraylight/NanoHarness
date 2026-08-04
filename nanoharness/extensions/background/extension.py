"""Schema-first Background extension."""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

from nanoharness.extensions.background.executor import (
    BackgroundExecutor,
    _DEFAULT_TIMEOUT,
)
from nanoharness.extensions.base import (
    BaseExtension,
    ExtensionContext,
    ExtensionInstallation,
    ExtensionManifest,
)


class BackgroundExtensionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_root: str = "."
    scratch_dir: Optional[str] = None
    service_name: str = "background"
    tool_prefix: str = ""
    max_concurrent: int = Field(default=4, ge=1, le=64)
    default_timeout: int = Field(default=_DEFAULT_TIMEOUT, ge=1, le=86400)
    max_preview_lines: int = Field(default=20, ge=1, le=1000)
    shell_command: List[str] = Field(default_factory=lambda: ["bash", "-c"], min_length=1)
    restrict_cwd: bool = True
    shutdown_timeout: float = Field(default=5.0, gt=0, le=60)


def _tool_name(prefix: str, name: str) -> str:
    return f"{prefix}{name}" if prefix else name


def register_background_tools(
    registry,
    bg_executor: BackgroundExecutor,
    *,
    tool_prefix: str = "",
    default_timeout: int = _DEFAULT_TIMEOUT,
) -> List[str]:
    run_name = _tool_name(tool_prefix, "background_run")
    poll_name = _tool_name(tool_prefix, "background_poll")

    def background_run(args: Dict[str, Any]) -> str:
        command = args.get("command", "")
        if not command:
            raise RuntimeError("command is required")
        task_id = bg_executor.run(
            command,
            cwd=args.get("cwd"),
            timeout=int(args.get("timeout", default_timeout)),
        )
        return (
            f"Started background task #{task_id}: {command}\n"
            f"Use {poll_name}(id={task_id}) to check status."
        )

    def background_poll(args: Dict[str, Any]) -> str:
        if args.get("id") is None:
            raise RuntimeError("id is required")
        task_id = int(args["id"])
        result = bg_executor.poll(task_id)
        if result is None:
            raise RuntimeError(f"Task {task_id} not found")
        if result["status"] == "running":
            return f"Task #{task_id} still running: {result['command']}"
        notification = bg_executor.notification(task_id)
        assert notification is not None
        return notification["message"]

    registry.register(
        name=run_name,
        handler=background_run,
        schema={
            "type": "function",
            "function": {
                "name": run_name,
                "description": (
                    "Run a shell command in the background and return a task ID. "
                    "Completion notifications can be drained by the host."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "command": {"type": "string", "description": "Shell command"},
                        "timeout": {"type": "integer", "description": "Timeout in seconds"},
                        "cwd": {"type": "string", "description": "Working directory"},
                    },
                    "required": ["command"],
                },
            },
        },
    )
    registry.register(
        name=poll_name,
        handler=background_poll,
        schema={
            "type": "function",
            "function": {
                "name": poll_name,
                "description": "Check the status and output of a background task.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "integer", "description": "Background task ID"},
                    },
                    "required": ["id"],
                },
            },
        },
    )
    return [run_name, poll_name]


class BackgroundExtension(BaseExtension):
    manifest = ExtensionManifest(
        name="background.shell",
        version="1.0.0",
        description="Managed background shell commands and completion notifications.",
        provides=[
            "background.executor",
            "notifications.background",
            "notifications.source",
            "tools.background",
        ],
    )
    config_model = BackgroundExtensionConfig

    def install(self, context: ExtensionContext, config: BaseModel) -> ExtensionInstallation:
        if not isinstance(config, BackgroundExtensionConfig):
            raise TypeError("BackgroundExtension requires BackgroundExtensionConfig")
        expected = {
            _tool_name(config.tool_prefix, "background_run"),
            _tool_name(config.tool_prefix, "background_poll"),
        }
        conflicts = sorted(expected & context.tool_names())
        if conflicts:
            raise ValueError(f"BackgroundExtension tool conflicts: {conflicts}")
        if config.service_name in context.services:
            raise ValueError(
                f"BackgroundExtension service conflicts: {config.service_name!r}"
            )
        executor = BackgroundExecutor(
            workspace_root=config.workspace_root,
            scratch_dir=config.scratch_dir,
            max_concurrent=config.max_concurrent,
            shell_command=config.shell_command,
            restrict_cwd=config.restrict_cwd,
            max_preview_lines=config.max_preview_lines,
            shutdown_timeout=config.shutdown_timeout,
        )
        tools = register_background_tools(
            context,
            executor,
            tool_prefix=config.tool_prefix,
            default_timeout=config.default_timeout,
        )
        context.provide_service(config.service_name, executor)
        return ExtensionInstallation(
            name=self.manifest.name,
            version=self.manifest.version,
            capabilities=list(self.manifest.provides),
            tools=tools,
            services=[config.service_name],
            config=config.model_dump(mode="json"),
            metadata={
                "workspace_root": executor._workspace_root,
                "scratch_dir": executor._scratch_dir,
                "max_concurrent": config.max_concurrent,
                "notification_source": True,
            },
        )

    def close(
        self,
        context: ExtensionContext,
        installation: ExtensionInstallation,
    ) -> None:
        for service_name in installation.services:
            service = context.services.get(service_name)
            if isinstance(service, BackgroundExecutor):
                service.close()
