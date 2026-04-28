"""Tests for MCP client, plugin loader, and tool registration.

Uses a fake MCP server (Python script speaking JSON-RPC over stdio)
instead of requiring real external servers like npx.
"""

import json
import os
import sys
import textwrap
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, _ROOT)

import pytest

from app.mcp import MCPClient, PluginLoader, mcp_handler, register_mcp_tools
from app.dispatch import DispatchRegistry, tool_result


# ── Fake MCP server ──

# This script speaks JSON-RPC over stdio, simulating an MCP server.
# It responds to initialize, tools/list, and tools/call.
FAKE_SERVER_SCRIPT = textwrap.dedent("""\
import sys
import json

def respond(id_, result):
    msg = {"jsonrpc": "2.0", "id": id_, "result": result}
    sys.stdout.write(json.dumps(msg) + "\\n")
    sys.stdout.flush()

def respond_error(id_, message):
    msg = {"jsonrpc": "2.0", "id": id_, "error": {"code": -32600, "message": message}}
    sys.stdout.write(json.dumps(msg) + "\\n")
    sys.stdout.flush()

TOOLS = [
    {
        "name": "read_file",
        "description": "Read a file",
        "inputSchema": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    },
    {
        "name": "list_files",
        "description": "List files in a directory",
        "inputSchema": {
            "type": "object",
            "properties": {"dir": {"type": "string"}},
        },
    },
]

for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        msg = json.loads(line)
    except json.JSONDecodeError:
        continue

    method = msg.get("method", "")
    id_ = msg.get("id")
    params = msg.get("params", {})

    if method == "initialize":
        respond(id_, {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "fake-mcp-server", "version": "0.1.0"},
        })
    elif method == "notifications/initialized":
        pass  # no response for notifications
    elif method == "tools/list":
        respond(id_, {"tools": TOOLS})
    elif method == "tools/call":
        tool_name = params.get("name", "")
        arguments = params.get("arguments", {})
        if tool_name == "read_file":
            path = arguments.get("path", "")
            respond(id_, {
                "content": [{"type": "text", "text": f"Contents of {path}"}]
            })
        elif tool_name == "list_files":
            d = arguments.get("dir", ".")
            respond(id_, {
                "content": [{"type": "text", "text": f"Files in {d}: a.txt, b.txt"}]
            })
        else:
            respond_error(id_, f"Unknown tool: {tool_name}")
    else:
        respond_error(id_, f"Unknown method: {method}")
""")


@pytest.fixture
def fake_server_script(tmp_path):
    """Write the fake MCP server script to a temp file."""
    script = tmp_path / "fake_mcp_server.py"
    script.write_text(FAKE_SERVER_SCRIPT)
    return str(script)


@pytest.fixture
def mcp_client(fake_server_script):
    """A connected MCPClient using the fake server."""
    client = MCPClient(
        name="test-server",
        command=sys.executable,
        args=[fake_server_script],
    )
    client.connect()
    yield client
    client.disconnect()


@pytest.fixture
def mcp_config(tmp_path):
    """Write a mcp_servers.json config that uses the fake server."""
    return None  # overridden per test


def _write_config(tmp_path, fake_server_script):
    """Helper: write config file pointing at the fake server."""
    config = {
        "servers": [
            {
                "name": "testfs",
                "command": sys.executable,
                "args": [fake_server_script],
                "transport": "stdio",
            }
        ]
    }
    config_path = str(tmp_path / "mcp_servers.json")
    with open(config_path, "w") as f:
        json.dump(config, f)
    return config_path


# ── TestMCPClient ──


class TestMCPClient:

    def test_connect_starts_process(self, mcp_client):
        assert mcp_client.connected

    def test_list_tools_returns_schemas(self, mcp_client):
        tools = mcp_client.list_tools()

        assert len(tools) == 2
        names = {t["name"] for t in tools}
        assert "read_file" in names
        assert "list_files" in names

    def test_call_tool_returns_result(self, mcp_client):
        result = mcp_client.call_tool("read_file", {"path": "/tmp/test.txt"})
        assert "Contents of /tmp/test.txt" in result

    def test_call_list_files(self, mcp_client):
        result = mcp_client.call_tool("list_files", {"dir": "/tmp"})
        assert "Files in /tmp" in result

    def test_disconnect_stops_process(self, mcp_client):
        mcp_client.disconnect()
        assert not mcp_client.connected

    def test_call_on_disconnected_raises(self, fake_server_script):
        client = MCPClient(
            name="disconnected",
            command=sys.executable,
            args=[fake_server_script],
        )
        # Never connected
        with pytest.raises(RuntimeError, match="not connected"):
            client.call_tool("read_file", {})

    def test_connect_bad_command_raises(self):
        client = MCPClient(
            name="bad",
            command="/nonexistent/command",
            args=[],
        )
        with pytest.raises(FileNotFoundError):
            client.connect()


# ── TestPluginLoader ──


class TestPluginLoader:

    def test_load_servers_reads_config(self, tmp_path, fake_server_script):
        config_path = _write_config(tmp_path, fake_server_script)
        loader = PluginLoader(config_path)
        servers = loader.load_servers()

        assert len(servers) == 1
        assert servers[0]["name"] == "testfs"
        assert servers[0]["command"] == sys.executable

    def test_missing_config_returns_empty(self, tmp_path):
        loader = PluginLoader(str(tmp_path / "nonexistent.json"))
        servers = loader.load_servers()
        assert servers == []

    def test_normalizes_missing_fields(self, tmp_path):
        config = {"servers": [{"name": "minimal", "command": "echo"}]}
        config_path = str(tmp_path / "mcp.json")
        with open(config_path, "w") as f:
            json.dump(config, f)

        loader = PluginLoader(config_path)
        servers = loader.load_servers()

        assert servers[0]["args"] == []
        assert servers[0]["transport"] == "stdio"
        assert servers[0]["env"] == {}


# ── TestMCPHandlerFactory ──


class TestMCPHandlerFactory:

    def test_handler_success(self, mcp_client):
        handler = mcp_handler(mcp_client, "read_file")
        result = handler({"path": "/tmp/hello.txt"})

        assert result.ok
        assert "Contents of /tmp/hello.txt" in result.output

    def test_handler_failure(self, fake_server_script):
        # Create client but don't connect — should fail
        client = MCPClient(
            name="dead",
            command=sys.executable,
            args=[fake_server_script],
        )
        handler = mcp_handler(client, "read_file")
        result = handler({"path": "/tmp/test.txt"})

        assert not result.ok
        assert result.error  # Should have error message


# ── TestMCPRegistration ──


class TestMCPRegistration:

    def test_register_mcp_tools_creates_prefixed_tools(
        self, tmp_path, fake_server_script
    ):
        config_path = _write_config(tmp_path, fake_server_script)
        registry = DispatchRegistry(workspace_root="/tmp")

        clients = register_mcp_tools(registry=registry, config_path=config_path)

        assert len(clients) == 1
        schemas = registry.schemas
        assert "mcp__testfs__read_file" in schemas
        assert "mcp__testfs__list_files" in schemas

        # Cleanup
        for c in clients:
            c.disconnect()

    def test_register_mcp_tools_schema_format(
        self, tmp_path, fake_server_script
    ):
        config_path = _write_config(tmp_path, fake_server_script)
        registry = DispatchRegistry(workspace_root="/tmp")

        clients = register_mcp_tools(registry=registry, config_path=config_path)

        schema = registry.schemas["mcp__testfs__read_file"]
        assert schema["type"] == "function"
        assert schema["function"]["name"] == "mcp__testfs__read_file"
        assert "path" in schema["function"]["parameters"]["properties"]

        for c in clients:
            c.disconnect()

    def test_register_mcp_tools_missing_config(self, tmp_path):
        registry = DispatchRegistry(workspace_root="/tmp")
        clients = register_mcp_tools(
            registry=registry,
            config_path=str(tmp_path / "nonexistent.json"),
        )
        assert clients == []

    def test_register_mcp_tools_bad_server_graceful(self, tmp_path):
        config = {
            "servers": [
                {
                    "name": "bad-server",
                    "command": "/nonexistent/command",
                    "args": [],
                }
            ]
        }
        config_path = str(tmp_path / "mcp.json")
        with open(config_path, "w") as f:
            json.dump(config, f)

        registry = DispatchRegistry(workspace_root="/tmp")
        # Should not raise — gracefully skips bad server
        clients = register_mcp_tools(registry=registry, config_path=config_path)
        assert clients == []


# ── TestMCPIntegration ──


class TestMCPIntegration:

    def test_end_to_end_via_dispatch(self, tmp_path, fake_server_script):
        """Config → PluginLoader → MCPClient → register → call via DispatchRegistry."""
        config_path = _write_config(tmp_path, fake_server_script)
        registry = DispatchRegistry(workspace_root="/tmp")

        clients = register_mcp_tools(registry=registry, config_path=config_path)

        # Call through the dispatch registry (same path the engine uses)
        result = registry.call("mcp__testfs__read_file", {"path": "/tmp/e2e.txt"})
        assert "Contents of /tmp/e2e.txt" in result

        for c in clients:
            c.disconnect()
