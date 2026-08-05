"""Schema-first Worktree extension with an explicit Task dependency."""

import subprocess
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

from nanoharness.extensions.base import (
    BaseExtension,
    ExtensionContext,
    ExtensionInstallation,
    ExtensionManifest,
)
from nanoharness.extensions.worktrees.registry import WorktreeRegistry


class WorktreeExtensionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_root: str = "."
    service_name: str = "worktrees"
    task_service_name: str = "tasks"
    tool_prefix: str = ""
    worktree_dir: str = ".worktrees"
    index_path: Optional[str] = None
    branch_prefix: str = "wt/"
    git_executable: str = "git"
    shell_command: List[str] = Field(default_factory=lambda: ["bash", "-c"], min_length=1)
    command_timeout: int = Field(default=30, ge=1, le=86400)


def _tool_name(prefix: str, name: str) -> str:
    return f"{prefix}{name}" if prefix else name


def register_worktree_tools(
    registry,
    wt_registry: WorktreeRegistry,
    *,
    tool_prefix: str = "",
) -> List[str]:
    names = {
        key: _tool_name(tool_prefix, f"worktree_{key}")
        for key in ("create", "enter", "run", "closeout", "list")
    }

    def worktree_create(args: Dict[str, Any]) -> str:
        name = args.get("name", "").strip()
        if not name:
            raise RuntimeError("name is required")
        if args.get("task_id") is None:
            raise RuntimeError("task_id is required")
        record = wt_registry.create(name, task_id=int(args["task_id"]))
        return (
            f"Created worktree '{record['name']}' "
            f"(branch: {record['branch']}, path: {record['path']}) "
            f"for task #{args['task_id']}"
        )

    def worktree_enter(args: Dict[str, Any]) -> str:
        name = args.get("name", "").strip()
        if not name:
            raise RuntimeError("name is required")
        record = wt_registry.enter(name)
        return f"Entered worktree '{name}' at {record['path']}"

    def worktree_run(args: Dict[str, Any]) -> str:
        name = args.get("name", "").strip()
        command = args.get("command", "").strip()
        if not name:
            raise RuntimeError("name is required")
        if not command:
            raise RuntimeError("command is required")
        try:
            exit_code, output = wt_registry.run(
                name,
                command,
                timeout=(
                    int(args["timeout"])
                    if args.get("timeout") is not None
                    else None
                ),
            )
        except subprocess.TimeoutExpired as error:
            raise RuntimeError(str(error)) from error
        if exit_code != 0:
            raise RuntimeError(
                f"Command exited with code {exit_code}: {output.strip()}"
            )
        return f"[OK] {output}"

    def worktree_closeout(args: Dict[str, Any]) -> str:
        name = args.get("name", "").strip()
        action = args.get("action", "").strip()
        if not name:
            raise RuntimeError("name is required")
        if action not in ("keep", "remove"):
            raise RuntimeError("action must be 'keep' or 'remove'")
        reason = args.get("reason", "").strip()
        wt_registry.closeout(
            name,
            action=action,
            reason=reason,
            complete_task=bool(args.get("complete_task", False)),
        )
        message = f"Closeout '{name}': {action}"
        if reason:
            message += f" — {reason}"
        return message

    def worktree_list(args: Dict[str, Any]) -> str:
        status = args.get("status", "").strip() or None
        records = wt_registry.list(status=status)
        if not records:
            return "No worktrees found."
        lines = []
        for record in records:
            task = (
                f" task=#{record['task_id']}"
                if record.get("task_id")
                else ""
            )
            lines.append(
                f"  {record['name']} [{record['status']}] "
                f"branch={record['branch']}{task}"
            )
        return "\n".join(lines)

    definitions = [
        (
            names["create"], worktree_create,
            "Create a Git worktree bound to a task.",
            {
                "name": {"type": "string"},
                "task_id": {"type": "integer"},
            },
            ["name", "task_id"],
        ),
        (
            names["enter"], worktree_enter,
            "Mark a worktree as entered.",
            {"name": {"type": "string"}},
            ["name"],
        ),
        (
            names["run"], worktree_run,
            "Run a shell command inside an isolated worktree.",
            {
                "name": {"type": "string"},
                "command": {"type": "string"},
                "timeout": {"type": "integer"},
            },
            ["name", "command"],
        ),
        (
            names["closeout"], worktree_closeout,
            "Keep or remove a worktree and update its bound task.",
            {
                "name": {"type": "string"},
                "action": {"type": "string", "enum": ["keep", "remove"]},
                "reason": {"type": "string"},
                "complete_task": {"type": "boolean"},
            },
            ["name", "action"],
        ),
        (
            names["list"], worktree_list,
            "List worktrees with an optional status filter.",
            {
                "status": {
                    "type": "string",
                    "enum": ["active", "kept", "removed"],
                }
            },
            [],
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


class WorktreeExtension(BaseExtension):
    manifest = ExtensionManifest(
        name="worktrees.git",
        version="1.0.0",
        description="Git worktree execution lanes bound to Task Board records.",
        provides=["worktrees.registry", "tools.worktrees"],
        requires=["tasks.board"],
    )
    config_model = WorktreeExtensionConfig

    def install(self, context: ExtensionContext, config: BaseModel) -> ExtensionInstallation:
        if not isinstance(config, WorktreeExtensionConfig):
            raise TypeError("WorktreeExtension requires WorktreeExtensionConfig")
        expected = {
            _tool_name(config.tool_prefix, f"worktree_{name}")
            for name in ("create", "enter", "run", "closeout", "list")
        }
        conflicts = sorted(expected & context.tool_names())
        if conflicts:
            raise ValueError(f"WorktreeExtension tool conflicts: {conflicts}")
        if config.service_name in context.services:
            raise ValueError(
                f"WorktreeExtension service conflicts: {config.service_name!r}"
            )
        task_board = context.services.get(config.task_service_name)
        if task_board is None:
            raise ValueError(
                "WorktreeExtension requires task service "
                f"{config.task_service_name!r}"
            )
        worktrees = WorktreeRegistry(
            workspace_root=config.workspace_root,
            task_board=task_board,
            index_path=config.index_path,
            worktree_dir=config.worktree_dir,
            branch_prefix=config.branch_prefix,
            git_executable=config.git_executable,
            shell_command=config.shell_command,
            command_timeout=config.command_timeout,
        )
        tools = register_worktree_tools(
            context,
            worktrees,
            tool_prefix=config.tool_prefix,
        )
        context.provide_service(config.service_name, worktrees)
        return ExtensionInstallation(
            name=self.manifest.name,
            version=self.manifest.version,
            capabilities=list(self.manifest.provides),
            tools=tools,
            services=[config.service_name],
            config=config.model_dump(mode="json"),
            metadata={
                "workspace_root": worktrees._root,
                "worktree_dir": worktrees._wt_dir,
                "index_path": worktrees._index_path,
                "task_service": config.task_service_name,
                "worktree_count": len(worktrees.list()),
            },
        )
