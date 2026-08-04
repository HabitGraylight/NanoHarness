import pytest
from pydantic import BaseModel, ConfigDict

from nanoharness.components.tools.dict_registry import DictToolRegistry
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
