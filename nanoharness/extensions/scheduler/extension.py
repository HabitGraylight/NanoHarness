"""Schema-first local Scheduler extension."""

import time
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

from nanoharness.extensions.base import (
    BaseExtension,
    ExtensionContext,
    ExtensionInstallation,
    ExtensionManifest,
)
from nanoharness.extensions.scheduler.scheduler import Scheduler


class SchedulerExtensionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    persist_path: Optional[str] = None
    service_name: str = "scheduler"
    tool_prefix: str = ""
    check_interval_seconds: float = Field(default=60.0, gt=0, le=3600)
    start_checker: bool = True
    shutdown_timeout: float = Field(default=5.0, gt=0, le=60)


def _tool_name(prefix: str, name: str) -> str:
    return f"{prefix}{name}" if prefix else name


def register_schedule_tools(
    registry,
    scheduler: Scheduler,
    *,
    tool_prefix: str = "",
) -> List[str]:
    names = {
        key: _tool_name(tool_prefix, f"schedule_{key}")
        for key in ("create", "list", "pause", "resume", "delete")
    }

    def schedule_create(args: Dict[str, Any]) -> str:
        prompt = args.get("prompt", "")
        if not prompt:
            raise ValueError("prompt is required")
        cron = args.get("cron")
        delay_seconds = args.get("delay_seconds")
        if not cron and delay_seconds is None:
            raise ValueError("Either cron or delay_seconds is required")
        schedule = scheduler.create(
            prompt=prompt,
            cron=cron,
            delay_seconds=(
                int(delay_seconds) if delay_seconds is not None else None
            ),
            max_fires=(
                int(args["max_fires"])
                if args.get("max_fires") is not None
                else None
            ),
            metadata=args.get("metadata"),
        )
        when = (
            f"cron: {schedule['cron']}"
            if schedule["cron"]
            else f"fires in {delay_seconds}s"
        )
        return f"Created schedule #{schedule['id']}: {prompt}\n  {when}"

    def schedule_list(args: Dict[str, Any]) -> str:
        schedules = scheduler.list(status=args.get("status"))
        if not schedules:
            return "No schedules found."
        lines = []
        for schedule in schedules:
            when = schedule["cron"] or (
                "one-shot at "
                + time.strftime(
                    "%H:%M:%S",
                    time.localtime(schedule["fire_at"]),
                )
            )
            lines.append(
                f"  #{schedule['id']} [{schedule['status']}] "
                f"{schedule['prompt'][:60]} — {when} "
                f"(fired {schedule['fire_count']}x)"
            )
        return "\n".join(lines)

    def schedule_pause(args: Dict[str, Any]) -> str:
        if args.get("id") is None:
            raise ValueError("id is required")
        schedule = scheduler.pause(int(args["id"]))
        return f"Paused schedule #{schedule['id']}: {schedule['prompt'][:60]}"

    def schedule_resume(args: Dict[str, Any]) -> str:
        if args.get("id") is None:
            raise ValueError("id is required")
        schedule = scheduler.resume(int(args["id"]))
        return f"Resumed schedule #{schedule['id']}: {schedule['prompt'][:60]}"

    def schedule_delete(args: Dict[str, Any]) -> str:
        if args.get("id") is None:
            raise ValueError("id is required")
        schedule = scheduler.delete(int(args["id"]))
        return f"Deleted schedule #{schedule['id']}: {schedule['prompt'][:60]}"

    definitions = [
        (
            names["create"],
            schedule_create,
            "Schedule a prompt using cron or a one-shot delay.",
            {
                "prompt": {"type": "string", "description": "Prompt to fire"},
                "cron": {"type": "string", "description": "Five-field cron"},
                "delay_seconds": {"type": "integer", "description": "One-shot delay"},
                "max_fires": {"type": "integer", "description": "Maximum fires"},
                "metadata": {
                    "type": "object",
                    "description": "Transport-neutral host metadata returned on fire",
                },
            },
            ["prompt"],
        ),
        (
            names["list"],
            schedule_list,
            "List scheduled prompts.",
            {"status": {"type": "string", "description": "Status filter"}},
            [],
        ),
        (
            names["pause"],
            schedule_pause,
            "Pause a schedule.",
            {"id": {"type": "integer", "description": "Schedule ID"}},
            ["id"],
        ),
        (
            names["resume"],
            schedule_resume,
            "Resume a paused schedule.",
            {"id": {"type": "integer", "description": "Schedule ID"}},
            ["id"],
        ),
        (
            names["delete"],
            schedule_delete,
            "Delete a schedule.",
            {"id": {"type": "integer", "description": "Schedule ID"}},
            ["id"],
        ),
    ]
    for name, handler, description, properties, required in definitions:
        registry.register(
            name=name,
            handler=handler,
            schema={
                "type": "function",
                "function": {
                    "name": name,
                    "description": description,
                    "parameters": {
                        "type": "object",
                        "properties": properties,
                        "required": required,
                    },
                },
            },
        )
    return list(names.values())


class SchedulerExtension(BaseExtension):
    manifest = ExtensionManifest(
        name="scheduler.local",
        version="1.0.0",
        description="Persistent cron and delayed prompt scheduler.",
        provides=[
            "scheduler.service",
            "notifications.scheduler",
            "notifications.source",
            "tools.scheduler",
        ],
    )
    config_model = SchedulerExtensionConfig

    def install(self, context: ExtensionContext, config: BaseModel) -> ExtensionInstallation:
        if not isinstance(config, SchedulerExtensionConfig):
            raise TypeError("SchedulerExtension requires SchedulerExtensionConfig")
        expected = {
            _tool_name(config.tool_prefix, f"schedule_{name}")
            for name in ("create", "list", "pause", "resume", "delete")
        }
        conflicts = sorted(expected & context.tool_names())
        if conflicts:
            raise ValueError(f"SchedulerExtension tool conflicts: {conflicts}")
        if config.service_name in context.services:
            raise ValueError(
                f"SchedulerExtension service conflicts: {config.service_name!r}"
            )
        scheduler = Scheduler(
            persist_path=config.persist_path,
            check_interval_seconds=config.check_interval_seconds,
            start_checker=config.start_checker,
        )
        tools = register_schedule_tools(
            context,
            scheduler,
            tool_prefix=config.tool_prefix,
        )
        context.provide_service(config.service_name, scheduler)
        return ExtensionInstallation(
            name=self.manifest.name,
            version=self.manifest.version,
            capabilities=list(self.manifest.provides),
            tools=tools,
            services=[config.service_name],
            config=config.model_dump(mode="json"),
            metadata={
                "persist_path": config.persist_path,
                "checker_started": config.start_checker,
                "check_interval_seconds": config.check_interval_seconds,
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
            if isinstance(service, Scheduler):
                config = self.parse_config(installation.config)
                service.stop(join_timeout=config.shutdown_timeout)
