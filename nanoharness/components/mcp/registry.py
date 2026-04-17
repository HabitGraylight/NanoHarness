import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from nanoharness.components.mcp.client import MCPClient
from nanoharness.components.tools.dict_registry import DictToolRegistry


class MCPToolRegistry(DictToolRegistry):
    """Tool registry backed by MCP servers.

    Connects to MCP servers, discovers their tools, and exposes them
    through the standard BaseToolRegistry interface — fully compatible
    with NanoEngine.

    Usage:
        client = MCPClient()
        client.connect_stdio("myserver", "npx", args=["-y", "some-mcp-server"])

        registry = MCPToolRegistry(client)
        schemas = registry.get_tool_schemas()
        result = registry.call("some_tool", {"arg": "value"})

        # Merge with shell-script tools:
        scripts = ScriptToolRegistry("configs/scripts")
        scripts.merge(registry)
    """

    def __init__(
        self,
        client: Optional[MCPClient] = None,
        config_path: Optional[str] = None,
    ):
        super().__init__()
        self._client = client or MCPClient()
        self._owned_client = client is None  # we created it, we close it

        if config_path:
            self._client.connect_from_config(config_path)

        self._load_tools()

    def _load_tools(self):
        """Discover tools from connected MCP servers and register them."""
        for tool_info in self._client.list_tools():
            name = tool_info["name"]
            schema = tool_info["inputSchema"]

            # Convert to OpenAI-compatible tool schema
            openai_schema = dict(schema)
            openai_schema.setdefault("additionalProperties", False)

            self._tools[name] = {
                "func": lambda _name=name, **kwargs: self._client.call_tool(_name, kwargs),
                "schema": {
                    "type": "function",
                    "function": {
                        "name": name,
                        "description": tool_info["description"],
                        "parameters": openai_schema,
                    },
                },
            }

    def close(self):
        if self._owned_client:
            self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
