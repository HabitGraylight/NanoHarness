import sys
import time

import pytest
from pydantic import BaseModel, ConfigDict

from nanoharness.components.tools.dict_registry import DictToolRegistry
from nanoharness.core.schema import LLMResponse
from nanoharness.extensions import (
    BaseExtension,
    DuplicateExtensionError,
    ExtensionConflictError,
    ExtensionContext,
    ExtensionDependencyError,
    ExtensionInstallation,
    ExtensionManager,
    ExtensionManagerClosedError,
    ExtensionManifest,
    ExtensionShutdownError,
)
from nanoharness.extensions.mcp import MCPExtension
from nanoharness.extensions.memory import MemoryExtension
from nanoharness.extensions.skills import SkillRegistry, SkillsExtension
from nanoharness.extensions.background import BackgroundExtension
from nanoharness.extensions.scheduler import SchedulerExtension
from nanoharness.extensions.subagents import SubagentExtension
from nanoharness.extensions.tasks import TaskExtension
from nanoharness.extensions.teams import TeamExtension, TeammateManager
from nanoharness.extensions.worktrees import WorktreeExtension


class DemoConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str = "demo"


class DemoExtension(BaseExtension):
    config_model = DemoConfig

    def __init__(self, *, name="demo", provides=None, requires=None, conflicts=None):
        self.manifest = ExtensionManifest(
            name=name,
            version="1.2.3",
            description="Test extension",
            provides=provides or [f"capability.{name}"],
            requires=requires or [],
            conflicts=conflicts or [],
        )

    def install(self, context, config):
        context.provide_service(self.manifest.name, config.label)
        return ExtensionInstallation(
            name=self.manifest.name,
            version=self.manifest.version,
            capabilities=list(self.manifest.provides),
            services=[self.manifest.name],
            config=config.model_dump(mode="json"),
        )


def make_manager(capabilities=None):
    context = ExtensionContext(
        tools=DictToolRegistry(),
        capabilities=set(capabilities or []),
    )
    return ExtensionManager(context)


def test_extension_exposes_manifest_and_config_schema():
    extension = DemoExtension()

    description = extension.describe()

    assert description["manifest"]["name"] == "demo"
    assert description["manifest"]["provides"] == ["capability.demo"]
    assert "label" in description["config_schema"]["properties"]
    with pytest.raises(ValueError):
        extension.parse_config({"unknown": True})


def test_manager_checks_capability_dependencies_before_install():
    manager = make_manager()
    extension = DemoExtension(requires=["runtime.scheduler"])

    with pytest.raises(ExtensionDependencyError, match="runtime.scheduler"):
        manager.install(extension)

    assert manager.context.services == {}


def test_manager_rejects_capability_conflicts():
    manager = make_manager(["memory.remote"])
    extension = DemoExtension(conflicts=["memory.remote"])

    with pytest.raises(ExtensionConflictError, match="memory.remote"):
        manager.install(extension)


def test_manager_rejects_duplicate_extension_and_exposes_inventory():
    manager = make_manager()
    extension = DemoExtension()

    installation = manager.install(extension, {"label": "installed"})
    inventory = manager.inspect()

    assert installation.config == {"label": "installed"}
    assert inventory["capabilities"] == ["capability.demo"]
    assert inventory["extensions"][0]["name"] == "demo"
    assert inventory["services"] == ["demo"]
    with pytest.raises(DuplicateExtensionError):
        manager.install(extension)


def test_memory_extension_installs_service_and_callable_tools(tmp_path):
    manager = make_manager()

    installation = manager.install(
        MemoryExtension(),
        {"directory": str(tmp_path / "memory")},
    )
    registry = manager.context.tools

    saved = registry.call(
        "save_memory",
        {
            "topic": "python style",
            "content": "Use four spaces.",
            "description": "Formatting convention",
        },
    )
    recalled = registry.call("recall_memory", {"query": "python"})
    listed = registry.call("list_memories", {})

    assert "python_style.md" in saved
    assert "Use four spaces" in recalled
    assert "Formatting convention" in listed
    assert installation.tools == ["save_memory", "recall_memory", "list_memories"]
    assert manager.context.services["memory"].directory == tmp_path / "memory"
    assert set(installation.capabilities) == {"memory.store", "tools.memory"}


def test_memory_extension_config_controls_prefix_and_preview(tmp_path):
    manager = make_manager()
    manager.install(
        MemoryExtension(),
        {
            "directory": str(tmp_path / "memory"),
            "tool_prefix": "project_",
            "preview_chars": 4,
        },
    )
    registry = manager.context.tools
    registry.call(
        "project_save_memory",
        {"topic": "long", "content": "abcdefgh"},
    )

    result = registry.call("project_recall_memory", {"query": "abc"})

    assert "abcd..." in result
    assert {schema["function"]["name"] for schema in registry.get_tool_schemas()} == {
        "project_save_memory",
        "project_recall_memory",
        "project_list_memories",
    }


def test_memory_extension_rejects_tool_conflicts_without_partial_install(tmp_path):
    manager = make_manager()
    manager.context.register_tool(
        "recall_memory",
        lambda args: "existing",
        {
            "type": "function",
            "function": {
                "name": "recall_memory",
                "description": "Existing tool",
                "parameters": {"type": "object", "properties": {}},
            },
        },
    )

    with pytest.raises(ValueError, match="recall_memory"):
        manager.install(
            MemoryExtension(),
            {"directory": str(tmp_path / "memory")},
        )

    assert manager.context.services == {}
    assert manager.context.tool_names() == {"recall_memory"}


def test_schema_first_registry_registration_accepts_argument_dictionary():
    registry = DictToolRegistry()
    registry.register(
        name="join",
        handler=lambda args: f"{args['left']}:{args['right']}",
        schema={
            "type": "function",
            "function": {
                "name": "join",
                "description": "Join values",
                "parameters": {"type": "object", "properties": {}},
            },
        },
    )

    assert registry.call("join", {"left": "a", "right": "b"}) == "a:b"


def write_skill(path, *, name="review", description="Review code", body="Read first"):
    path.write_text(
        "---\n"
        f"name: {name}\n"
        f"description: {description}\n"
        "trigger: when reviewing\n"
        "---\n"
        f"# Instructions\n{body}\n",
        encoding="utf-8",
    )


def test_skills_extension_installs_registry_and_dynamic_tool(tmp_path):
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    write_skill(skills_dir / "review.md")
    manager = make_manager()

    installation = manager.install(
        SkillsExtension(),
        {"directory": str(skills_dir)},
    )
    result = manager.context.tools.call("skill", {"name": "review"})

    assert "[Skill: review]" in result
    assert "Read first" in result
    assert manager.context.services["skills"].list_names() == ["review"]
    assert set(installation.capabilities) == {"skills.registry", "tools.skills"}
    assert installation.metadata["skill_count"] == 1


def test_skills_tool_schema_contains_discovery_metadata(tmp_path):
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    write_skill(
        skills_dir / "debug.md",
        name="debugging",
        description="Diagnose failures",
    )
    manager = make_manager()
    manager.install(SkillsExtension(), {"directory": str(skills_dir)})

    schema = manager.context.tools.get_tool_schemas()[0]
    description = schema["function"]["description"]

    assert "debugging" in description
    assert "Diagnose failures" in description


def test_skills_extension_supports_custom_pattern_service_and_tool(tmp_path):
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    write_skill(skills_dir / "include.skill.md", name="included")
    write_skill(skills_dir / "exclude.md", name="excluded")
    manager = make_manager()

    installation = manager.install(
        SkillsExtension(),
        {
            "directory": str(skills_dir),
            "pattern": "*.skill.md",
            "service_name": "project_skills",
            "tool_name": "load_project_skill",
        },
    )

    assert installation.tools == ["load_project_skill"]
    assert installation.services == ["project_skills"]
    assert manager.context.services["project_skills"].list_names() == ["included"]


def test_skills_extension_rejects_tool_conflict_before_service_install(tmp_path):
    manager = make_manager()
    manager.context.register_tool(
        "skill",
        lambda args: "existing",
        {
            "type": "function",
            "function": {
                "name": "skill",
                "description": "Existing",
                "parameters": {"type": "object", "properties": {}},
            },
        },
    )

    with pytest.raises(ValueError, match="tool conflicts"):
        manager.install(
            SkillsExtension(),
            {"directory": str(tmp_path / "skills")},
        )

    assert "skills" not in manager.context.services


def test_skill_registry_reload_discovers_new_files(tmp_path):
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    registry = SkillRegistry(str(skills_dir))
    write_skill(skills_dir / "new.md", name="new-skill")

    registry.reload()

    assert registry.list_names() == ["new-skill"]


class ClosingDemoExtension(DemoExtension):
    def __init__(self, journal, *, fail_close=False, **kwargs):
        super().__init__(**kwargs)
        self.journal = journal
        self.fail_close = fail_close

    def close(self, context, installation):
        self.journal.append(installation.name)
        if self.fail_close:
            raise RuntimeError("close failed")


def test_extension_manager_closes_in_reverse_order_and_only_once():
    journal = []
    manager = make_manager()
    manager.install(ClosingDemoExtension(journal, name="first"))
    manager.install(ClosingDemoExtension(journal, name="second"))

    manager.close()
    manager.close()

    assert journal == ["second", "first"]
    assert manager.closed
    assert manager.inspect()["closed"] is True
    with pytest.raises(ExtensionManagerClosedError):
        manager.install(DemoExtension(name="late"))


def test_extension_manager_continues_closing_after_failure():
    journal = []
    manager = make_manager()
    manager.install(ClosingDemoExtension(journal, name="first"))
    manager.install(
        ClosingDemoExtension(journal, name="second", fail_close=True)
    )

    with pytest.raises(ExtensionShutdownError, match="second: close failed"):
        manager.close()

    assert journal == ["second", "first"]
    assert manager.closed


class FakeMCPClient:
    instances = []

    def __init__(self, name, command, **kwargs):
        self.name = name
        self.command = command
        self.connected = False
        self.calls = []
        self.__class__.instances.append(self)

    def connect(self):
        if self.command == "broken":
            raise RuntimeError("cannot start")
        self.connected = True

    def list_tools(self):
        return [
            {
                "name": "echo",
                "description": "Echo input",
                "inputSchema": {
                    "type": "object",
                    "properties": {"text": {"type": "string"}},
                    "required": ["text"],
                },
            }
        ]

    def call_tool(self, name, arguments=None):
        self.calls.append((name, arguments))
        return arguments["text"]

    def disconnect(self):
        self.connected = False


@pytest.fixture
def fake_mcp_client(monkeypatch):
    FakeMCPClient.instances = []
    monkeypatch.setattr(
        "nanoharness.extensions.mcp.extension.MCPClient",
        FakeMCPClient,
    )
    return FakeMCPClient


def test_mcp_extension_discovers_tools_redacts_env_and_closes(fake_mcp_client):
    manager = make_manager()

    installation = manager.install(
        MCPExtension(),
        {
            "servers": [
                {
                    "name": "demo",
                    "command": "fake",
                    "env": {"API_TOKEN": "super-secret"},
                }
            ]
        },
    )
    result = manager.context.tools.call("mcp__demo__echo", {"text": "hello"})

    assert result == "hello"
    assert installation.tools == ["mcp__demo__echo"]
    assert installation.config["servers"][0]["env"] == {"API_TOKEN": "***"}
    assert installation.metadata["connected_servers"] == ["demo"]
    assert set(installation.capabilities) == {"mcp.clients", "tools.mcp"}
    assert manager.context.services["mcp"].connected_names == ["demo"]

    manager.close()
    assert not fake_mcp_client.instances[0].connected


def test_mcp_extension_preflights_dynamic_tool_conflicts(fake_mcp_client):
    manager = make_manager()
    manager.context.register_tool(
        "mcp__demo__echo",
        lambda args: "existing",
        {
            "type": "function",
            "function": {
                "name": "mcp__demo__echo",
                "description": "Existing",
                "parameters": {"type": "object", "properties": {}},
            },
        },
    )

    with pytest.raises(ValueError, match="tool conflicts"):
        manager.install(
            MCPExtension(),
            {"servers": [{"name": "demo", "command": "fake"}]},
        )

    assert "mcp" not in manager.context.services
    assert not fake_mcp_client.instances[0].connected


def test_mcp_extension_records_unavailable_servers(fake_mcp_client):
    manager = make_manager()

    with pytest.warns(RuntimeWarning, match="cannot start"):
        installation = manager.install(
            MCPExtension(),
            {"servers": [{"name": "offline", "command": "broken"}]},
        )

    assert installation.tools == []
    assert installation.metadata["failed_servers"] == ["offline"]
    assert manager.context.services["mcp"].failures == {
        "offline": "cannot start"
    }


def test_mcp_extension_fail_fast_closes_connected_servers(fake_mcp_client):
    manager = make_manager()

    with pytest.raises(RuntimeError, match="offline.*cannot start"):
        manager.install(
            MCPExtension(),
            {
                "fail_fast": True,
                "servers": [
                    {"name": "online", "command": "fake"},
                    {"name": "offline", "command": "broken"},
                ],
            },
        )

    assert "mcp" not in manager.context.services
    assert all(not client.connected for client in fake_mcp_client.instances)


def wait_for_background(executor, task_id, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        result = executor.poll(task_id)
        if result and result["status"] != "running":
            return result
        time.sleep(0.02)
    raise TimeoutError(f"Background task {task_id} did not finish")


def test_background_extension_installs_tools_service_and_notifications(tmp_path):
    manager = make_manager()
    installation = manager.install(
        BackgroundExtension(),
        {
            "workspace_root": str(tmp_path),
            "scratch_dir": str(tmp_path / "scratch"),
            "shell_command": [sys.executable, "-c"],
        },
    )

    started = manager.context.tools.call(
        "background_run",
        {"command": "print('extension output')"},
    )
    executor = manager.context.services["background"]
    result = wait_for_background(executor, 1)
    notification = executor.drain()[0]

    assert "Started background task #1" in started
    assert result["status"] == "completed"
    assert "extension output" in notification["message"]
    assert installation.tools == ["background_run", "background_poll"]
    assert set(installation.capabilities) == {
        "background.executor",
        "notifications.background",
        "notifications.source",
        "tools.background",
    }
    manager.close()
    assert executor.closed


def test_background_extension_close_cancels_running_process(tmp_path):
    manager = make_manager()
    manager.install(
        BackgroundExtension(),
        {
            "workspace_root": str(tmp_path),
            "shell_command": [sys.executable, "-c"],
        },
    )
    executor = manager.context.services["background"]
    task_id = executor.run("import time; time.sleep(30)", timeout=60)

    manager.close()

    assert executor.poll(task_id)["status"] == "cancelled"
    assert executor.closed


def test_background_extension_confines_working_directory(tmp_path):
    manager = make_manager()
    manager.install(
        BackgroundExtension(),
        {
            "workspace_root": str(tmp_path / "workspace"),
            "shell_command": [sys.executable, "-c"],
        },
    )
    executor = manager.context.services["background"]

    with pytest.raises(PermissionError, match="escapes workspace"):
        executor.run("print('no')", cwd=str(tmp_path))

    manager.close()


def test_scheduler_extension_installs_persistent_tools_and_service(tmp_path):
    manager = make_manager()
    installation = manager.install(
        SchedulerExtension(),
        {
            "persist_path": str(tmp_path / "schedules.json"),
            "start_checker": False,
        },
    )

    created = manager.context.tools.call(
        "schedule_create",
        {"prompt": "Review changes", "delay_seconds": 60},
    )
    listed = manager.context.tools.call("schedule_list", {})
    scheduler = manager.context.services["scheduler"]

    assert "Created schedule #1" in created
    assert "Review changes" in listed
    assert installation.metadata["notification_source"] is True
    assert set(installation.capabilities) == {
        "notifications.scheduler",
        "notifications.source",
        "scheduler.service",
        "tools.scheduler",
    }
    manager.close()
    assert scheduler.closed
    with pytest.raises(RuntimeError, match="closed"):
        scheduler.create("Late", delay_seconds=1)


def test_scheduler_extension_close_joins_checker_thread():
    manager = make_manager()
    manager.install(
        SchedulerExtension(),
        {"check_interval_seconds": 60},
    )
    scheduler = manager.context.services["scheduler"]
    assert scheduler.checker_alive

    manager.close()

    assert not scheduler.checker_alive


def test_scheduler_extension_rejects_conflicts_before_starting_service():
    manager = make_manager()
    manager.context.register_tool(
        "schedule_list",
        lambda args: "existing",
        {
            "type": "function",
            "function": {
                "name": "schedule_list",
                "description": "Existing",
                "parameters": {"type": "object", "properties": {}},
            },
        },
    )

    with pytest.raises(ValueError, match="tool conflicts"):
        manager.install(SchedulerExtension())

    assert "scheduler" not in manager.context.services


def test_task_extension_installs_board_and_dependency_tools(tmp_path):
    manager = make_manager()
    installation = manager.install(
        TaskExtension(),
        {"persist_path": str(tmp_path / "tasks.json")},
    )

    created = manager.context.tools.call(
        "task_create",
        {"subject": "Design protocol"},
    )
    listed = manager.context.tools.call("task_list", {})

    assert "Created task #1" in created
    assert "Design protocol" in listed
    assert manager.context.services["tasks"].get(1)["subject"] == "Design protocol"
    assert installation.tools == [
        "task_create",
        "task_list",
        "task_update",
        "task_complete",
    ]
    assert set(installation.capabilities) == {"tasks.board", "tools.tasks"}


def test_worktree_extension_requires_task_capability(tmp_path):
    manager = make_manager()

    with pytest.raises(ExtensionDependencyError, match="tasks.board"):
        manager.install(
            WorktreeExtension(),
            {"workspace_root": str(tmp_path)},
        )

    assert not (tmp_path / ".worktrees").exists()


def test_task_then_worktree_extensions_expose_resolved_dependency_graph(tmp_path):
    manager = make_manager()
    manager.install(TaskExtension())
    installation = manager.install(
        WorktreeExtension(),
        {"workspace_root": str(tmp_path)},
    )

    inventory = manager.inspect()

    assert [item["name"] for item in inventory["extensions"]] == [
        "tasks.board",
        "worktrees.git",
    ]
    assert inventory["dependencies"] == [
        {
            "extension": "worktrees.git",
            "capability": "tasks.board",
            "providers": ["tasks.board"],
        }
    ]
    assert inventory["extensions"][1]["requires"] == ["tasks.board"]
    assert installation.tools == [
        "worktree_create",
        "worktree_enter",
        "worktree_run",
        "worktree_closeout",
        "worktree_list",
    ]
    assert manager.context.services["worktrees"]._task_board is (
        manager.context.services["tasks"]
    )
    assert (tmp_path / ".worktrees").is_dir()


def test_worktree_extension_rejects_service_mapping_mismatch(tmp_path):
    manager = make_manager()
    manager.install(TaskExtension())

    with pytest.raises(ValueError, match="requires task service"):
        manager.install(
            WorktreeExtension(),
            {
                "workspace_root": str(tmp_path),
                "task_service_name": "project_tasks",
            },
        )

    assert "worktrees" not in manager.context.services
    assert not (tmp_path / ".worktrees").exists()


def test_worktree_extension_preflights_tools_before_filesystem_changes(tmp_path):
    manager = make_manager()
    manager.install(TaskExtension())
    manager.context.register_tool(
        "worktree_list",
        lambda args: "existing",
        {
            "type": "function",
            "function": {
                "name": "worktree_list",
                "description": "Existing",
                "parameters": {"type": "object", "properties": {}},
            },
        },
    )

    with pytest.raises(ValueError, match="tool conflicts"):
        manager.install(
            WorktreeExtension(),
            {"workspace_root": str(tmp_path)},
        )

    assert not (tmp_path / ".worktrees").exists()


class ImmediateLLM:
    def chat(self, messages, tools=None):
        return LLMResponse(content="delegated result")


class ParentContext:
    def get_full_context(self):
        return [{"role": "system", "content": "parent context"}]


def test_subagent_extension_requires_host_runtime_capabilities():
    manager = make_manager()

    with pytest.raises(
        ExtensionDependencyError,
        match="runtime.agent_llm.*runtime.context",
    ):
        manager.install(SubagentExtension())

    assert "task" not in manager.context.tool_names()


def test_subagent_extension_resolves_host_services_and_delegates():
    manager = make_manager(["runtime.agent_llm", "runtime.context"])
    manager.context.provide_service("llm.agent", ImmediateLLM())
    manager.context.provide_service("context.agent", ParentContext())

    installation = manager.install(SubagentExtension())
    result = manager.context.tools.call(
        "task",
        {"description": "inspect the project", "fork": True},
    )
    inventory = manager.inspect()

    assert result == "delegated result"
    assert installation.services == ["subagents"]
    assert installation.metadata["lifecycle"] == "one-shot"
    assert inventory["dependencies"] == [
        {
            "extension": "subagents.delegate",
            "capability": "runtime.agent_llm",
            "providers": ["context"],
        },
        {
            "extension": "subagents.delegate",
            "capability": "runtime.context",
            "providers": ["context"],
        },
    ]


def test_team_extension_requires_llm_and_task_board(tmp_path):
    manager = make_manager(["runtime.llm"])
    manager.context.metadata["workspace_root"] = str(tmp_path)
    manager.context.provide_service("llm.raw", ImmediateLLM())

    with pytest.raises(ExtensionDependencyError, match="tasks.board"):
        manager.install(TeamExtension())

    assert "team" not in manager.context.services


def test_team_extension_installs_dependency_graph_and_closes_threads(tmp_path):
    manager = make_manager(["runtime.llm"])
    manager.context.metadata["workspace_root"] = str(tmp_path)
    manager.context.provide_service("llm.raw", ImmediateLLM())
    manager.install(TaskExtension())
    installation = manager.install(
        TeamExtension(),
        {
            "check_interval": 0.01,
            "idle_check_interval": 0.01,
            "idle_max_checks": 1,
            "shutdown_timeout": 1,
        },
    )
    team = manager.context.services["team"]

    spawned = manager.context.tools.call(
        "team_spawn",
        {"name": "reviewer", "role": "reviewer"},
    )
    inventory = manager.inspect()

    assert "Spawned teammate 'reviewer'" in spawned
    assert team.active_thread_names == ["reviewer"]
    assert installation.metadata["lifecycle"] == "long-lived"
    assert {
        (edge["capability"], tuple(edge["providers"]))
        for edge in inventory["dependencies"]
        if edge["extension"] == "teams.runtime"
    } == {
        ("runtime.llm", ("context",)),
        ("tasks.board", ("tasks.board",)),
    }

    manager.close()

    assert team.closed
    assert team.active_thread_names == []
    assert team.list()[0]["status"] == "shutdown"


def test_team_runtime_confines_storage_and_teammate_names(tmp_path):
    registry = DictToolRegistry()

    with pytest.raises(ValueError, match="within workspace_root"):
        TeammateManager(
            llm_client=ImmediateLLM(),
            registry=registry,
            workspace_root=str(tmp_path / "workspace"),
            team_dir=str(tmp_path / "outside"),
        )

    manager = make_manager(["runtime.llm"])
    manager.context.metadata["workspace_root"] = str(tmp_path / "workspace")
    manager.context.provide_service("llm.raw", ImmediateLLM())
    manager.install(TaskExtension())
    manager.install(TeamExtension())

    with pytest.raises(RuntimeError, match="safe characters"):
        manager.context.tools.call(
            "team_spawn",
            {"name": "../escape", "role": "reviewer"},
        )
    with pytest.raises(ValueError, match="Invalid request ID"):
        manager.context.services["team"]._tracker.get("../../escape")

    manager.close()
