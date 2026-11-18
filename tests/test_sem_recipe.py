"""Portable recipe wiring checks; no Sem binary or model key is required."""

import importlib.util
import json
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

from nanoharness.extensions.mcp import MCPClient


TOOLS = [{
    "name": "sem_find",
    "inputSchema": {
        "type": "object",
        "properties": {"query": {"type": "string"}},
        "required": ["query"],
    },
}]


@pytest.fixture
def recipe():
    path = Path(__file__).resolve().parents[1] / "recipes/sem_code/inspect_repo.py"
    spec = importlib.util.spec_from_file_location("sem_recipe", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def mock_mcp(monkeypatch):
    client = Mock()
    client.name = "sem"
    client.connected = True
    client.list_tools.return_value = TOOLS
    client.call_tool.return_value = "function parse_config src/config.py:1"
    factory = Mock(return_value=client)
    monkeypatch.setattr("nanoharness.extensions.mcp.extension.MCPClient", factory)
    return factory, client


def test_recipe_uses_explicit_local_process_and_closes(recipe, mock_mcp, tmp_path, capsys):
    factory, client = mock_mcp

    recipe.main([str(tmp_path), "function parse_config"])

    config = factory.call_args.kwargs
    assert config["command"] == "sem"
    assert config["args"] == ["mcp"]
    assert config["cwd"] == str(tmp_path.resolve())
    assert config["env"] == {
        "SEM_REPO": str(tmp_path.resolve()),
        "SEM_LOCAL": "1",
        "SEM_MCP_NO_SHARED": "1",
    }
    assert config["read_timeout_seconds"] == 60
    client.call_tool.assert_called_once_with("sem_find", {"query": "function parse_config"})
    client.disconnect.assert_called_once_with()
    assert capsys.readouterr().out.strip() == "function parse_config src/config.py:1"


@pytest.mark.parametrize("error", [FileNotFoundError("sem not found"), RuntimeError("initialize failed")])
def test_recipe_fails_fast_on_startup_error(recipe, mock_mcp, tmp_path, error):
    _, client = mock_mcp
    client.connect.side_effect = error

    with pytest.raises(RuntimeError, match="MCP server 'sem' failed") as raised:
        recipe.main([str(tmp_path), "parse_config"])

    assert raised.value.__cause__ is error
    client.call_tool.assert_not_called()
    client.disconnect.assert_called_once_with()


def test_recipe_closes_after_tool_error(recipe, mock_mcp, tmp_path):
    _, client = mock_mcp
    client.call_tool.side_effect = RuntimeError("invalid Git repository")

    with pytest.raises(RuntimeError, match="invalid Git repository"):
        recipe.main([str(tmp_path), "parse_config"])

    client.disconnect.assert_called_once_with()


def test_recipe_closes_when_sem_find_is_not_advertised(recipe, mock_mcp, tmp_path):
    _, client = mock_mcp
    client.list_tools.return_value = []

    with pytest.raises(KeyError, match="mcp__sem__sem_find"):
        recipe.main([str(tmp_path), "parse_config"])

    client.disconnect.assert_called_once_with()


@pytest.mark.parametrize("kind", ["missing", "file"])
def test_recipe_rejects_invalid_path_before_startup(recipe, mock_mcp, tmp_path, capsys, kind):
    factory, _ = mock_mcp
    path = tmp_path / kind
    if kind == "file":
        path.write_text("not a directory", encoding="utf-8")

    with pytest.raises(SystemExit) as raised:
        recipe.main([str(path), "parse_config"])

    assert raised.value.code == 2
    assert "error:" in capsys.readouterr().err
    factory.assert_not_called()


def test_recipe_help_does_not_start_mcp(recipe, mock_mcp, capsys):
    factory, _ = mock_mcp

    with pytest.raises(SystemExit) as raised:
        recipe.main(["--help"])

    assert raised.value.code == 0
    assert "Entity name to find" in capsys.readouterr().out
    factory.assert_not_called()


def test_recipe_rejects_empty_query_before_startup(recipe, mock_mcp, tmp_path, capsys):
    factory, _ = mock_mcp

    with pytest.raises(SystemExit) as raised:
        recipe.main([str(tmp_path), "   "])

    assert raised.value.code == 2
    assert "query must not be empty" in capsys.readouterr().err
    factory.assert_not_called()


def test_recipe_through_real_stdio_with_local_server(recipe, monkeypatch, tmp_path, capsys):
    pytest.importorskip("mcp")
    server = tmp_path / "server.py"
    server.write_text(
        "import json, os, sys\n"
        "for line in sys.stdin:\n"
        "    message = json.loads(line)\n"
        "    if 'id' not in message:\n"
        "        continue\n"
        "    method = message['method']\n"
        "    if method == 'initialize':\n"
        "        result = {'protocolVersion': '2024-11-05', 'capabilities': {'tools': {}},\n"
        "                  'serverInfo': {'name': 'fixture-sem', 'version': '1'}}\n"
        "    elif method == 'tools/list':\n"
        f"        result = {{'tools': {TOOLS!r}}}\n"
        "    elif method == 'tools/call':\n"
        "        assert message['params']['name'] == 'sem_find'\n"
        "        payload = {'query': message['params']['arguments']['query'],\n"
        "                   'repo': os.environ['SEM_REPO'], 'cwd': os.getcwd(),\n"
        "                   'local': os.environ['SEM_LOCAL'],\n"
        "                   'no_shared': os.environ['SEM_MCP_NO_SHARED']}\n"
        "        result = {'content': [{'type': 'text', 'text': json.dumps(payload)}]}\n"
        "    print(json.dumps({'jsonrpc': '2.0', 'id': message['id'], 'result': result}), flush=True)\n",
        encoding="utf-8",
    )
    clients = []

    def connect_fixture(**config):
        config.update(command=sys.executable, args=[str(server)])
        client = MCPClient(**config)
        clients.append(client)
        return client

    monkeypatch.setattr("nanoharness.extensions.mcp.extension.MCPClient", connect_fixture)

    recipe.main([str(tmp_path), "parse_config"])

    assert json.loads(capsys.readouterr().out) == {
        "query": "parse_config",
        "repo": str(tmp_path.resolve()),
        "cwd": str(tmp_path.resolve()),
        "local": "1",
        "no_shared": "1",
    }
    assert len(clients) == 1
    assert not clients[0].connected
