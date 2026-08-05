"""Schema-first Task Board extension."""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict

from nanoharness.extensions.base import (
    BaseExtension,
    ExtensionContext,
    ExtensionInstallation,
    ExtensionManifest,
)
from nanoharness.extensions.tasks.board import TaskBoard, is_ready


class TaskExtensionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    persist_path: Optional[str] = None
    service_name: str = "tasks"
    tool_prefix: str = ""


def _tool_name(prefix: str, name: str) -> str:
    return f"{prefix}{name}" if prefix else name


def register_task_tools(
    registry,
    board: TaskBoard,
    *,
    tool_prefix: str = "",
) -> List[str]:
    names = {
        key: _tool_name(tool_prefix, f"task_{key}")
        for key in ("create", "list", "update", "complete")
    }

    def task_create(args: Dict[str, Any]) -> str:
        subject = args.get("subject", "")
        if not subject:
            raise RuntimeError("subject is required")
        blocked_by = args.get("blockedBy") or []
        if isinstance(blocked_by, str):
            blocked_by = [
                int(value) for value in blocked_by.split(",") if value.strip()
            ]
        task = board.add(
            subject=subject,
            description=args.get("description", ""),
            blocked_by=blocked_by,
            owner=args.get("owner", ""),
            required_role=args.get("required_role"),
        )
        suffix = (
            f"\n  blockedBy: {task['blockedBy']}"
            if task["blockedBy"]
            else ""
        )
        return (
            f"Created task #{task['id']}: {task['subject']} "
            f"[{task['status']}]{suffix}"
        )

    def task_list(args: Dict[str, Any]) -> str:
        tasks = board.list(status=args.get("status"), owner=args.get("owner"))
        if not tasks:
            return "No tasks found."
        lines = []
        for task in tasks:
            blocker = (
                f" (blocked by {task['blockedBy']})"
                if task["blockedBy"]
                else ""
            )
            owner = f" [{task['owner']}]" if task["owner"] else ""
            lines.append(
                f"  #{task['id']} [{task['status']}] "
                f"{task['subject']}{owner}{blocker}"
            )
        return "\n".join(lines)

    def task_update(args: Dict[str, Any]) -> str:
        if args.get("id") is None:
            raise RuntimeError("id is required")
        task = board.update(
            task_id=int(args["id"]),
            subject=args.get("subject"),
            description=args.get("description"),
            owner=args.get("owner"),
            add_blocked_by=args.get("addBlockedBy"),
            add_blocks=args.get("addBlocks"),
        )
        return f"Updated task #{task['id']}: {task['subject']}"

    def task_complete(args: Dict[str, Any]) -> str:
        if args.get("id") is None:
            raise RuntimeError("id is required")
        task = board.complete(int(args["id"]))
        unblocked = [
            f"#{blocked_id}"
            for blocked_id in task["blocks"]
            if board.get(blocked_id) and is_ready(board.get(blocked_id))
        ]
        message = f"Completed task #{task['id']}: {task['subject']}"
        if unblocked:
            message += f"\n  Unblocked: {', '.join(unblocked)}"
        return message

    definitions = [
        (
            names["create"], task_create,
            "Create a task with optional dependency links.",
            {
                "subject": {"type": "string", "description": "Task title"},
                "description": {"type": "string", "description": "Details"},
                "blockedBy": {"type": "array", "items": {"type": "integer"}},
                "owner": {"type": "string", "description": "Owner"},
                "required_role": {"type": "string", "description": "Claim role"},
            },
            ["subject"],
        ),
        (
            names["list"], task_list, "List tasks with optional filters.",
            {
                "status": {
                    "type": "string",
                    "enum": ["pending", "in_progress", "completed", "deleted"],
                },
                "owner": {"type": "string"},
            },
            [],
        ),
        (
            names["update"], task_update, "Update task fields or dependencies.",
            {
                "id": {"type": "integer"},
                "subject": {"type": "string"},
                "description": {"type": "string"},
                "owner": {"type": "string"},
                "addBlockedBy": {"type": "array", "items": {"type": "integer"}},
                "addBlocks": {"type": "array", "items": {"type": "integer"}},
            },
            ["id"],
        ),
        (
            names["complete"], task_complete,
            "Complete a task and unblock its dependents.",
            {"id": {"type": "integer"}},
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


class TaskExtension(BaseExtension):
    manifest = ExtensionManifest(
        name="tasks.board",
        version="1.0.0",
        description="Persistent task board with dependency and claim tracking.",
        provides=["tasks.board", "tools.tasks"],
    )
    config_model = TaskExtensionConfig

    def install(self, context: ExtensionContext, config: BaseModel) -> ExtensionInstallation:
        if not isinstance(config, TaskExtensionConfig):
            raise TypeError("TaskExtension requires TaskExtensionConfig")
        expected = {
            _tool_name(config.tool_prefix, f"task_{name}")
            for name in ("create", "list", "update", "complete")
        }
        conflicts = sorted(expected & context.tool_names())
        if conflicts:
            raise ValueError(f"TaskExtension tool conflicts: {conflicts}")
        if config.service_name in context.services:
            raise ValueError(f"TaskExtension service conflicts: {config.service_name!r}")
        board = TaskBoard(persist_path=config.persist_path)
        tools = register_task_tools(
            context,
            board,
            tool_prefix=config.tool_prefix,
        )
        context.provide_service(config.service_name, board)
        return ExtensionInstallation(
            name=self.manifest.name,
            version=self.manifest.version,
            capabilities=list(self.manifest.provides),
            tools=tools,
            services=[config.service_name],
            config=config.model_dump(mode="json"),
            metadata={
                "persist_path": config.persist_path,
                "task_count": len(board.list()),
            },
        )
