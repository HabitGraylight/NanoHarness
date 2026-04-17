"""Tests for MCP integration using a local mock MCP server."""

import json
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from nanoharness.components.mcp.client import MCPClient
from nanoharness.components.mcp.registry import MCPToolRegistry
from nanoharness.components.tools.dict_registry import DictToolRegistry
from nanoharness.components.tools.script_tools import ScriptToolRegistry


# ── Minimal mock MCP server ──

MOCK_SERVER_SCRIPT = '''
import asyncio
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("test-server")

@mcp.tool()
def add(a: int, b: int) -> str:
    """Add two numbers together."""
    return str(a + b)

@mcp.tool()
def greet(name: str) -> str:
    """Greet someone by name."""
    return f"Hello, {name}!"

mcp.run()
'''


@pytest.fixture
def mock_server_script(tmp_path):
    """Write the mock MCP server to a temp file."""
    script = tmp_path / "mock_server.py"
    script.write_text(MOCK_SERVER_SCRIPT)
    return str(script)


@pytest.fixture
def mcp_config(tmp_path, mock_server_script):
    """Write a config pointing at the mock server."""
    config = {
        "servers": [
            {
                "name": "test",
                "command": sys.executable,
                "args": [mock_server_script],
                "transport": "stdio",
            }
        ]
    }
    config_path = tmp_path / "mcp_servers.json"
    config_path.write_text(json.dumps(config))
    return str(config_path)


class TestMCPClient:
    def test_connect_and_list_tools(self, mock_server_script):
        with MCPClient() as client:
            client.connect_stdio("test", sys.executable, args=[mock_server_script])
            tools = client.list_tools()
            names = [t["name"] for t in tools]
            assert "add" in names
            assert "greet" in names

    def test_call_tool(self, mock_server_script):
        with MCPClient() as client:
            client.connect_stdio("test", sys.executable, args=[mock_server_script])
            result = client.call_tool("add", {"a": 3, "b": 4})
            assert "7" in result

    def test_call_tool_greet(self, mock_server_script):
        with MCPClient() as client:
            client.connect_stdio("test", sys.executable, args=[mock_server_script])
            result = client.call_tool("greet", {"name": "World"})
            assert "Hello, World!" in result

    def test_tool_not_found(self, mock_server_script):
        with MCPClient() as client:
            client.connect_stdio("test", sys.executable, args=[mock_server_script])
            with pytest.raises(KeyError, match="nonexistent"):
                client.call_tool("nonexistent", {})

    def test_connect_from_config(self, mcp_config):
        with MCPClient() as client:
            client.connect_from_config(mcp_config)
            tools = client.list_tools()
            assert len(tools) >= 2


class TestMCPToolRegistry:
    def test_loads_tools_from_client(self, mock_server_script):
        with MCPClient() as client:
            client.connect_stdio("test", sys.executable, args=[mock_server_script])
            registry = MCPToolRegistry(client)
            schemas = registry.get_tool_schemas()
            names = [s["function"]["name"] for s in schemas]
            assert "add" in names
            assert "greet" in names

    def test_call_via_registry(self, mock_server_script):
        with MCPClient() as client:
            client.connect_stdio("test", sys.executable, args=[mock_server_script])
            registry = MCPToolRegistry(client)
            result = registry.call("add", {"a": 10, "b": 20})
            assert "30" in result

    def test_schema_is_openai_compatible(self, mock_server_script):
        with MCPClient() as client:
            client.connect_stdio("test", sys.executable, args=[mock_server_script])
            registry = MCPToolRegistry(client)
            for schema in registry.get_tool_schemas():
                assert schema["type"] == "function"
                func = schema["function"]
                assert "name" in func
                assert "description" in func
                assert "parameters" in func
                params = func["parameters"]
                assert params.get("type") == "object"
                assert "additionalProperties" in params

    def test_from_config(self, mcp_config):
        client = MCPClient()
        registry = MCPToolRegistry(config_path=mcp_config)
        schemas = registry.get_tool_schemas()
        assert len(schemas) >= 2
        registry.close()
        client.close()


class TestMergeRegistries:
    def test_merge_mcp_into_scripts(self, mock_server_script):
        scripts = ScriptToolRegistry("configs/scripts")
        script_count = len(scripts.get_tool_schemas())

        with MCPClient() as client:
            client.connect_stdio("test", sys.executable, args=[mock_server_script])
            mcp_reg = MCPToolRegistry(client)
            scripts.merge(mcp_reg)

        total = len(scripts.get_tool_schemas())
        assert total == script_count + len(mcp_reg.get_tool_schemas())

        # MCP tools are callable through the merged registry
        assert "add" in [s["function"]["name"] for s in scripts.get_tool_schemas()]

    def test_merge_preserves_script_tools(self, mock_server_script):
        scripts = ScriptToolRegistry("configs/scripts")

        with MCPClient() as client:
            client.connect_stdio("test", sys.executable, args=[mock_server_script])
            mcp_reg = MCPToolRegistry(client)
            scripts.merge(mcp_reg)

        # Original script tools still work
        names = [s["function"]["name"] for s in scripts.get_tool_schemas()]
        assert "git_status" in names
        assert "file_read" in names
        assert "add" in names  # MCP tool
