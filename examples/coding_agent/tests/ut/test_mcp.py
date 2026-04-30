"""Tests for MCP plugin loader and handler factory."""

import json
import sys
import textwrap

import pytest

from app.mcp import MCPClient, PluginLoader, mcp_handler
from app.dispatch import DispatchRegistry, tool_result


# -- Fake MCP server script --

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


# -- TestPluginLoader --


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


# -- TestMCPHandlerFactory --


class TestMCPHandlerFactory:

    def test_handler_success(self, mcp_client):
        handler = mcp_handler(mcp_client, "read_file")
        result = handler({"path": "/tmp/hello.txt"})

        assert result.ok
        assert "Contents of /tmp/hello.txt" in result.output

    def test_handler_failure(self, fake_server_script):
        # Create client but don't connect -- should fail
        client = MCPClient(
            name="dead",
            command=sys.executable,
            args=[fake_server_script],
        )
        handler = mcp_handler(client, "read_file")
        result = handler({"path": "/tmp/test.txt"})

        assert not result.ok
        assert result.error  # Should have error message
