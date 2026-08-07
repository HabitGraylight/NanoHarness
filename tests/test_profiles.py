import json

import pytest

from nanoharness.components.context import SimpleContextManager
from nanoharness.components.evaluator import TraceEvaluator
from nanoharness.components.hooks import SimpleHookManager
from nanoharness.components.state import JsonStateStore
from nanoharness.components.tools import DictToolRegistry
from nanoharness.core.schema import LLMResponse
from nanoharness.extensions import (
    BaseExtension,
    ExtensionContext,
    ExtensionInstallation,
    ExtensionManifest,
)
from nanoharness.profiles import (
    DuplicateCatalogEntryError,
    ExtensionCatalog,
    HarnessBuilder,
    HarnessSpec,
    HarnessSpecError,
    load_harness_spec,
)
from nanoharness.profiles.cli import main as profile_cli


class NoopExtension(BaseExtension):
    def __init__(self, name, *, provides=None, requires=None, conflicts=None):
        self.manifest = ExtensionManifest(
            name=name,
            version="1.0.0",
            provides=provides or [f"cap.{name}"],
            requires=requires or [],
            conflicts=conflicts or [],
        )

    def install(self, context, config):
        return ExtensionInstallation(
            name=self.manifest.name,
            version=self.manifest.version,
            capabilities=list(self.manifest.provides),
        )


class ImmediateLLM:
    def chat(self, messages, tools=None):
        return LLMResponse(content="hosted delegation")


class ParentContext:
    def get_full_context(self):
        return [{"role": "system", "content": "host context"}]


def test_builtin_catalog_exposes_all_public_extensions():
    catalog = ExtensionCatalog.builtins()

    assert catalog.names() == [
        "background.shell",
        "channels.durable",
        "mcp.stdio",
        "memory.file",
        "scheduler.local",
        "skills.directory",
        "subagents.delegate",
        "tasks.board",
        "teams.runtime",
        "worktrees.git",
    ]
    with pytest.raises(DuplicateCatalogEntryError):
        catalog.register(lambda: NoopExtension("tasks.board"))


def test_harness_spec_loads_yaml_and_toml(tmp_path):
    yaml_path = tmp_path / "profile.yaml"
    yaml_path.write_text(
        "name: yaml-profile\n"
        "extensions:\n"
        "  - name: tasks.board\n",
        encoding="utf-8",
    )
    toml_path = tmp_path / "profile.toml"
    toml_path.write_text(
        'schema_version = "1.0"\n'
        'name = "toml-profile"\n'
        '[[extensions]]\n'
        'name = "tasks.board"\n',
        encoding="utf-8",
    )

    assert load_harness_spec(str(yaml_path)).name == "yaml-profile"
    assert HarnessSpec.from_file(str(toml_path)).extensions[0].name == "tasks.board"


def test_validation_reorders_extensions_by_capability_dependency(tmp_path):
    spec = HarnessSpec.model_validate({
        "name": "task-lanes",
        "extensions": [
            {
                "name": "worktrees.git",
                "config": {"workspace_root": str(tmp_path)},
            },
            {"name": "tasks.board"},
        ],
    })

    result = HarnessBuilder().validate(spec)

    assert result.valid
    assert result.installation_order == ["tasks.board", "worktrees.git"]
    assert result.dependencies[0].model_dump() == {
        "extension": "worktrees.git",
        "capability": "tasks.board",
        "providers": ["tasks.board"],
    }
    assert not (tmp_path / ".worktrees").exists()


def test_validation_reports_unknown_duplicate_and_invalid_config():
    spec = HarnessSpec.model_validate({
        "name": "broken",
        "extensions": [
            {"name": "missing.extension"},
            {"name": "background.shell", "config": {"max_concurrent": 0}},
            {"name": "tasks.board"},
            {"name": "tasks.board"},
        ],
    })

    result = HarnessBuilder().validate(spec)
    codes = {issue.code for issue in result.errors}

    assert not result.valid
    assert codes == {
        "duplicate_extension",
        "invalid_extension_config",
        "unknown_extension",
    }
    assert all("0" not in issue.message for issue in result.errors)


def test_validation_reports_missing_capability_and_unsupported_version():
    spec = HarnessSpec.model_validate({
        "schema_version": "2.0",
        "name": "missing-task-board",
        "host": {
            "capabilities": ["runtime.llm"],
            "services": ["llm.raw"],
        },
        "extensions": [
            {"name": "teams.runtime", "config": {"workspace_root": "."}},
        ],
    })

    result = HarnessBuilder().validate(spec)

    assert not result.valid
    assert {issue.code for issue in result.errors} == {
        "missing_capability",
        "unsupported_spec_version",
    }


def test_real_context_must_supply_declared_host_bindings():
    spec = HarnessSpec.model_validate({
        "name": "hosted-subagent",
        "host": {
            "capabilities": ["runtime.agent_llm", "runtime.context"],
            "services": ["llm.agent", "context.agent"],
        },
        "extensions": [{"name": "subagents.delegate"}],
    })
    builder = HarnessBuilder()
    offline = builder.validate(spec)
    context = ExtensionContext(tools=DictToolRegistry())

    runtime = builder.validate(spec, context=context)

    assert offline.valid
    assert {warning.code for warning in offline.warnings} == {
        "offline_host_assumptions"
    }
    assert {issue.code for issue in runtime.errors} == {
        "missing_host_capability",
        "missing_host_service",
    }


def test_hosted_profile_builds_when_real_bindings_match_declaration():
    spec = HarnessSpec.model_validate({
        "name": "hosted-subagent",
        "host": {
            "capabilities": ["runtime.agent_llm", "runtime.context"],
            "services": ["llm.agent", "context.agent"],
        },
        "extensions": [{"name": "subagents.delegate"}],
    })
    context = ExtensionContext(
        tools=DictToolRegistry(),
        capabilities={"runtime.agent_llm", "runtime.context"},
        services={
            "llm.agent": ImmediateLLM(),
            "context.agent": ParentContext(),
        },
    )

    build = HarnessBuilder().build(spec, context=context)

    assert context.tools.call("task", {"description": "inspect"}) == (
        "hosted delegation"
    )
    assert build.validation.dependencies[0].providers == ["host"]
    build.close()


def test_custom_catalog_detects_dependency_cycle_and_conflict():
    catalog = ExtensionCatalog()
    catalog.register(lambda: NoopExtension(
        "cycle.a", provides=["cap.a"], requires=["cap.b"]
    ))
    catalog.register(lambda: NoopExtension(
        "cycle.b", provides=["cap.b"], requires=["cap.a"]
    ))
    catalog.register(lambda: NoopExtension(
        "conflict.c", provides=["cap.c"], conflicts=["cap.a"]
    ))
    spec = HarnessSpec.model_validate({
        "name": "cycle",
        "extensions": [
            {"name": "cycle.a"},
            {"name": "cycle.b"},
            {"name": "conflict.c"},
        ],
    })

    result = HarnessBuilder(catalog).validate(spec)

    assert not result.valid
    assert {issue.code for issue in result.errors} == {
        "capability_conflict",
        "dependency_cycle",
    }


def test_explain_is_white_box_and_redacts_nested_secrets():
    spec = HarnessSpec.model_validate({
        "name": "mcp-profile",
        "extensions": [{
            "name": "mcp.stdio",
            "config": {
                "servers": [{
                    "name": "demo",
                    "command": "server",
                    "env": {"API_TOKEN": "secret-value"},
                }],
            },
        }],
    })

    explanation = HarnessBuilder().explain(spec)
    extension = explanation.extensions[0]

    assert explanation.valid
    assert extension.provides == ["mcp.clients", "tools.mcp"]
    assert extension.config["servers"][0]["env"] == {"API_TOKEN": "***"}
    assert "secret-value" not in explanation.model_dump_json()
    assert "properties" in extension.config_schema


def test_build_installs_in_planned_order_and_returns_runtime_inventory(tmp_path):
    spec = HarnessSpec.model_validate({
        "name": "buildable",
        "extensions": [
            {
                "name": "worktrees.git",
                "config": {"workspace_root": str(tmp_path)},
            },
            {"name": "tasks.board"},
        ],
    })

    build = HarnessBuilder().build(spec)

    assert list(build.manager.installations) == ["tasks.board", "worktrees.git"]
    assert build.inspect()["validation"]["valid"] is True
    assert build.context.services["worktrees"]._task_board is (
        build.context.services["tasks"]
    )
    assert (tmp_path / ".worktrees").is_dir()
    build.close()


def test_build_can_bind_full_etcslv_engine_from_host_services(tmp_path):
    service_names = ["llm", "context", "state", "hooks", "evaluator"]
    spec = HarnessSpec.model_validate({
        "name": "full-engine",
        "host": {"services": service_names},
        "engine": {
            "llm_service": "llm",
            "context_service": "context",
            "state_service": "state",
            "hooks_service": "hooks",
            "evaluator_service": "evaluator",
            "max_steps": 7,
            "session_id": "profile-session",
        },
        "metadata": {"api_key": "engine-secret"},
    })
    context = ExtensionContext(
        tools=DictToolRegistry(),
        services={
            "llm": ImmediateLLM(),
            "context": SimpleContextManager(system_prompt="profile"),
            "state": JsonStateStore(str(tmp_path / "state.json")),
            "hooks": SimpleHookManager(),
            "evaluator": TraceEvaluator(),
        },
    )

    build = HarnessBuilder().build(spec, context=context)

    assert build.engine is not None
    assert build.engine.max_steps == 7
    assert build.engine.session_id == "profile-session"
    assert build.engine.extension_manager is build.manager
    assert build.inspect()["engine"] == {
        "type": "NanoEngine",
        "max_steps": 7,
        "session_id": "profile-session",
    }
    assert build.inspect()["profile"]["metadata"]["api_key"] == "***"
    assert "engine-secret" not in json.dumps(build.inspect())
    build.close()


def test_build_rejects_invalid_profile_before_installing():
    spec = HarnessSpec.model_validate({
        "name": "invalid-build",
        "extensions": [{"name": "worktrees.git"}],
    })
    context = ExtensionContext(tools=DictToolRegistry())

    with pytest.raises(HarnessSpecError):
        HarnessBuilder().build(spec, context=context)

    assert context.services == {}
    assert context.tool_names() == set()


def test_disabled_unknown_extension_is_ignored():
    spec = HarnessSpec.model_validate({
        "name": "disabled",
        "extensions": [{"name": "not-installed", "enabled": False}],
    })

    result = HarnessBuilder().validate(spec)

    assert result.valid
    assert result.installation_order == []
    assert {warning.code for warning in result.warnings} == {"empty_profile"}


def test_cli_validate_and_explain_emit_json(tmp_path, capsys):
    assert profile_cli(["catalog"]) == 0
    catalog = json.loads(capsys.readouterr().out)
    assert catalog[0]["manifest"]["name"] == "background.shell"
    assert "config_schema" in catalog[0]

    path = tmp_path / "profile.yaml"
    path.write_text(
        "name: cli-profile\nextensions:\n  - name: tasks.board\n",
        encoding="utf-8",
    )

    assert profile_cli(["validate", str(path), "--compact"]) == 0
    validation = json.loads(capsys.readouterr().out)
    assert validation["valid"] is True

    assert profile_cli(["explain", str(path), "--compact"]) == 0
    explanation = json.loads(capsys.readouterr().out)
    assert explanation["installation_order"] == ["tasks.board"]

    invalid = tmp_path / "invalid.yaml"
    invalid.write_text(
        "name: invalid-profile\nunknown: secret-value\n",
        encoding="utf-8",
    )
    assert profile_cli(["validate", str(invalid), "--compact"]) == 2
    failure = capsys.readouterr().out
    assert "spec_load_error" in failure
    assert "secret-value" not in failure
