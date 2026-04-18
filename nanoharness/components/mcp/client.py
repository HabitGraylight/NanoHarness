import asyncio
import json
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


class MCPClient:
    """Synchronous wrapper over the async MCP Python SDK.

    Spawns a background event loop thread to handle async MCP operations.
    All public methods are synchronous — safe to call from NanoEngine.
    """

    def __init__(self, timeout: int = 60):
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True)
        self._thread.start()
        self._sessions: Dict[str, Dict[str, Any]] = {}
        self._tool_to_server: Dict[str, str] = {}
        self._timeout = timeout

    # ── Connection ──

    def connect_stdio(
        self,
        name: str,
        command: str,
        args: Optional[List[str]] = None,
        env: Optional[Dict[str, str]] = None,
    ):
        params = StdioServerParameters(command=command, args=args or [], env=env)
        self._run(self._connect_session(name, params))

    def connect_from_config(self, config_path: str):
        path = Path(config_path)
        if not path.exists():
            return
        data = json.loads(path.read_text(encoding="utf-8"))
        for server in data.get("servers", []):
            if server.get("transport", "stdio") == "stdio":
                self.connect_stdio(
                    name=server["name"],
                    command=server["command"],
                    args=server.get("args", []),
                    env=server.get("env"),
                )

    # ── Tool Discovery ──

    def list_tools(self) -> List[Dict[str, Any]]:
        """List all tools from all connected servers."""
        return self._run(self._list_tools())

    # ── Tool Execution ──

    def call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> str:
        return self._run(self._call_tool(tool_name, arguments))

    # ── Lifecycle ──

    def close(self):
        self._run(self._close_all())
        self._loop.call_soon_threadsafe(self._loop.stop)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # ── Async internals ──

    async def _connect_session(self, name: str, params: StdioServerParameters):
        transport = stdio_client(params)
        read, write = await transport.__aenter__()
        session = ClientSession(read, write)
        await session.__aenter__()
        await session.initialize()

        tools_result = await session.list_tools()
        for tool in tools_result.tools:
            self._tool_to_server[tool.name] = name

        self._sessions[name] = {
            "session": session,
            "transport": transport,
            "tools": {t.name: t for t in tools_result.tools},
        }

    async def _list_tools(self):
        result = []
        for info in self._sessions.values():
            for tool in info["tools"].values():
                result.append({
                    "name": tool.name,
                    "description": tool.description or "",
                    "inputSchema": tool.inputSchema,
                })
        return result

    async def _call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> str:
        server_name = self._tool_to_server.get(tool_name)
        if not server_name:
            raise KeyError(
                f"MCP tool '{tool_name}' not found. "
                f"Available: {list(self._tool_to_server)}"
            )
        session = self._sessions[server_name]["session"]
        result = await session.call_tool(tool_name, arguments)
        if result.isError:
            err = "; ".join(c.text for c in result.content if hasattr(c, "text"))
            raise RuntimeError(f"MCP tool '{tool_name}' failed: {err}")
        return "\n".join(c.text for c in result.content if hasattr(c, "text"))

    async def _close_all(self):
        for info in self._sessions.values():
            try:
                await info["session"].__aexit__(None, None, None)
            except Exception:
                pass
        self._sessions.clear()
        self._tool_to_server.clear()

    def _run(self, coro):
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return future.result(timeout=self._timeout)
