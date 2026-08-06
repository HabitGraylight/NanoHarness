"""NanoClaudeCode adapter for the reusable MCP extension."""

from typing import Dict

from nanoharness.extensions.mcp import (
    MCPClient,
    PluginLoader,
    register_mcp_tools,
)
from nanoharness.extensions.mcp import mcp_handler as _portable_mcp_handler

from app.dispatch import tool_result


def mcp_handler(client: MCPClient, tool_name: str):
    """Adapt a portable MCP handler to the local ``tool_result`` API."""
    portable_handler = _portable_mcp_handler(client, tool_name)

    def handler(args: Dict) -> tool_result:
        try:
            return tool_result(ok=True, output=portable_handler(args))
        except Exception as error:
            return tool_result(ok=False, output="", error=str(error))

    return handler


__all__ = [
    "MCPClient",
    "PluginLoader",
    "mcp_handler",
    "register_mcp_tools",
]
