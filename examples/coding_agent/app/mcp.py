"""MCP (Model Context Protocol) client and plugin discovery.

Three-layer mental model:
    Plugin  = discovery (finds server configs in mcp_servers.json)
    MCP Server = connection (an external process speaking JSON-RPC over stdio)
    MCP Tool = invocation (a specific callable on a server)

Tool name convention: mcp__{server}__{tool}
Example: mcp__filesystem__read_file

Usage:
    client = MCPClient("filesystem", command="npx", args=["-y", "@modelcontextprotocol/server-filesystem", "/tmp"])
    client.connect()
    tools = client.list_tools()
    result = client.call_tool("read_file", {"path": "/tmp/hello.txt"})
    client.disconnect()
"""

import json
import os
import subprocess
import threading
import time
from typing import Any, Callable, Dict, List, Optional

from app.dispatch import DispatchRegistry, tool_result


# ── JSON-RPC helpers ──


_next_id = 1
_id_lock = threading.Lock()


def _next_rpc_id() -> int:
    global _next_id
    with _id_lock:
        _next_id += 1
        return _next_id - 1


def _make_request(method: str, params: Optional[Dict] = None) -> bytes:
    """Build a JSON-RPC 2.0 request message."""
    msg = {
        "jsonrpc": "2.0",
        "id": _next_rpc_id(),
        "method": method,
    }
    if params is not None:
        msg["params"] = params
    return (json.dumps(msg) + "\n").encode("utf-8")


def _make_notification(method: str, params: Optional[Dict] = None) -> bytes:
    """Build a JSON-RPC 2.0 notification (no id, no response expected)."""
    msg = {
        "jsonrpc": "2.0",
        "method": method,
    }
    if params is not None:
        msg["params"] = params
    return (json.dumps(msg) + "\n").encode("utf-8")


def _read_response(proc: subprocess.Popen, timeout: float = 15.0) -> Dict:
    """Read a single JSON-RPC response from the server's stdout."""
    import select

    deadline = time.time() + timeout
    buf = ""
    while time.time() < deadline:
        remaining = deadline - time.time()
        if remaining <= 0:
            raise TimeoutError("MCP server response timeout")

        # Check if process has exited
        if proc.poll() is not None:
            raise RuntimeError(
                f"MCP server exited with code {proc.returncode}: "
                f"{proc.stderr.read().decode('utf-8', errors='replace') if proc.stderr else ''}"
            )

        # Use select for non-blocking read with timeout
        ready, _, _ = select.select([proc.stdout], [], [], min(0.5, remaining))
        if ready:
            chunk = os.read(proc.stdout.fileno(), 4096).decode("utf-8", errors="replace")
            buf += chunk
            # Try to parse complete JSON messages
            while "\n" in buf:
                line, buf = buf.split("\n", 1)
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                    # Skip notifications (no id) — we're looking for responses
                    if "id" in msg:
                        return msg
                except json.JSONDecodeError:
                    continue

    raise TimeoutError("MCP server response timeout")


# ── MCPClient ──


class MCPClient:
    """Connect to a single MCP server via stdio transport.

    Manages a subprocess that speaks JSON-RPC over stdin/stdout.
    """

    def __init__(
        self,
        name: str,
        command: str,
        args: Optional[List[str]] = None,
        env: Optional[Dict[str, str]] = None,
    ):
        self.name = name
        self._command = command
        self._args = args or []
        self._env = env or {}
        self._proc: Optional[subprocess.Popen] = None
        self._tools: List[Dict] = []

    @property
    def tools(self) -> List[Dict]:
        """Cached tool schemas from last list_tools() call."""
        return list(self._tools)

    @property
    def connected(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def connect(self) -> None:
        """Start the MCP server process and perform handshake."""
        env = dict(os.environ)
        env.update(self._env)

        self._proc = subprocess.Popen(
            [self._command, *self._args],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
        )

        # MCP handshake: initialize → response → initialized notification
        init_params = {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "nanoharness-coding-agent", "version": "0.1.0"},
        }
        self._proc.stdin.write(_make_request("initialize", init_params))
        self._proc.stdin.flush()

        resp = _read_response(self._proc)
        if "error" in resp:
            raise RuntimeError(
                f"MCP initialize failed: {resp['error'].get('message', resp['error'])}"
            )

        # Send initialized notification
        self._proc.stdin.write(_make_notification("notifications/initialized"))
        self._proc.stdin.flush()

    def list_tools(self) -> List[Dict]:
        """Ask the server what tools it exposes. Returns tool schemas."""
        self._require_connected()
        self._proc.stdin.write(_make_request("tools/list"))
        self._proc.stdin.flush()

        resp = _read_response(self._proc)
        if "error" in resp:
            raise RuntimeError(
                f"MCP tools/list failed: {resp['error'].get('message', resp['error'])}"
            )

        self._tools = resp.get("result", {}).get("tools", [])
        return self._tools

    def call_tool(self, tool_name: str, arguments: Optional[Dict] = None) -> str:
        """Forward a tool invocation to the server. Returns result text."""
        self._require_connected()
        params = {"name": tool_name, "arguments": arguments or {}}
        self._proc.stdin.write(_make_request("tools/call", params))
        self._proc.stdin.flush()

        resp = _read_response(self._proc)
        if "error" in resp:
            raise RuntimeError(
                f"MCP tools/call failed: {resp['error'].get('message', resp['error'])}"
            )

        # MCP tool results: {"content": [{"type": "text", "text": "..."}, ...]}
        result = resp.get("result", {})
        content = result.get("content", [])
        text_parts = []
        for item in content:
            if item.get("type") == "text":
                text_parts.append(item.get("text", ""))
        return "\n".join(text_parts) if text_parts else json.dumps(result)

    def disconnect(self) -> None:
        """Gracefully shut down the server process."""
        if self._proc is None:
            return
        try:
            self._proc.stdin.close()
        except Exception:
            pass
        try:
            self._proc.terminate()
            self._proc.wait(timeout=5)
        except Exception:
            try:
                self._proc.kill()
            except Exception:
                pass
        self._proc = None

    def _require_connected(self):
        if not self.connected:
            raise RuntimeError(f"MCP server '{self.name}' is not connected")


# ── PluginLoader ──


class PluginLoader:
    """Discover MCP server configs from mcp_servers.json."""

    def __init__(self, config_path: str):
        self._config_path = config_path

    def load_servers(self) -> List[Dict]:
        """Read config file and return server config list.

        Returns empty list if file doesn't exist.
        Each entry: {"name", "command", "args", "transport", "env"}
        """
        if not os.path.exists(self._config_path):
            return []

        with open(self._config_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        servers = data.get("servers", [])
        # Normalize: ensure args and env defaults
        for srv in servers:
            srv.setdefault("args", [])
            srv.setdefault("transport", "stdio")
            srv.setdefault("env", {})
        return servers


# ── Handler factory ──


def mcp_handler(client: MCPClient, tool_name: str) -> Callable[[Dict], tool_result]:
    """Create a handler that forwards calls to an MCP server.

    Follows the same Callable[[Dict], tool_result] contract as
    bash_handler, bash_wrap, and inprocess_handler.
    """
    def handler(args: Dict) -> tool_result:
        try:
            result = client.call_tool(tool_name, args)
            return tool_result(ok=True, output=result)
        except Exception as e:
            return tool_result(ok=False, output="", error=str(e))
    return handler


# ── Tool registration ──


def register_mcp_tools(
    registry: DispatchRegistry,
    config_path: str,
) -> List[MCPClient]:
    """Discover MCP servers, connect, and register their tools.

    For each server in config:
      1. Create MCPClient, connect, list_tools
      2. For each tool: register as mcp__{server}__{tool}

    Returns connected clients for lifecycle management.
    """
    loader = PluginLoader(config_path)
    servers = loader.load_servers()
    clients: List[MCPClient] = []

    for srv_config in servers:
        name = srv_config["name"]
        command = srv_config["command"]
        args = srv_config.get("args", [])
        env = srv_config.get("env", {})

        client = MCPClient(
            name=name,
            command=command,
            args=args,
            env=env,
        )

        try:
            client.connect()
            tools = client.list_tools()
        except Exception as e:
            # Don't fail startup if one server is unavailable
            print(f"[MCP] Warning: server '{name}' failed: {e}")
            client.disconnect()
            continue

        for tool_def in tools:
            tool_name = tool_def.get("name", "")
            if not tool_name:
                continue

            prefixed_name = f"mcp__{name}__{tool_name}"

            # Build schema in the format DispatchRegistry expects
            description = tool_def.get("description", "")
            input_schema = tool_def.get("inputSchema", {"type": "object", "properties": {}})

            schema = {
                "type": "function",
                "function": {
                    "name": prefixed_name,
                    "description": description,
                    "parameters": input_schema,
                },
            }

            handler = mcp_handler(client, tool_name)
            registry.register(
                name=prefixed_name,
                handler=handler,
                schema=schema,
            )

        clients.append(client)

    return clients
