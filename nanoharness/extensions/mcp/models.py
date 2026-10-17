"""Validated configuration models for local and remote MCP servers."""

import json
from pathlib import Path
from typing import Dict, List, Literal, Optional
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, model_validator


class MCPServerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    command: Optional[str] = Field(default=None, min_length=1)
    args: List[str] = Field(default_factory=list)
    transport: Literal["stdio", "streamable_http"] = "stdio"
    env: Dict[str, str] = Field(default_factory=dict)
    cwd: Optional[str] = None
    url: Optional[str] = None
    headers: Dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_transport(self):
        if self.transport == "stdio":
            if not self.command:
                raise ValueError("stdio MCP servers require a command")
            if self.url is not None or self.headers:
                raise ValueError("url and headers require streamable_http transport")
        else:
            if not self.url:
                raise ValueError("streamable_http MCP servers require a URL")
            parsed = urlsplit(self.url)
            if (parsed.scheme not in ("http", "https") or not parsed.hostname
                    or parsed.username is not None or parsed.password is not None
                    or parsed.fragment):
                raise ValueError("MCP URL must be HTTP(S) without credentials or fragments")
            if self.command is not None or self.args or self.env or self.cwd is not None:
                raise ValueError("command, args, env and cwd require stdio transport")
        return self


def parallel_search_server() -> MCPServerConfig:
    """Anonymous search and page fetching, without reading ambient credentials."""
    return MCPServerConfig(
        name="parallel",
        transport="streamable_http",
        url="https://search.parallel.ai/mcp",
    )


class MCPServerFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    servers: List[MCPServerConfig] = Field(default_factory=list)


class PluginLoader:
    """Discover and validate MCP server definitions from a JSON file."""

    def __init__(self, config_path: str):
        self.config_path = Path(config_path)

    def load_server_configs(self) -> List[MCPServerConfig]:
        if not self.config_path.exists():
            return []
        data = json.loads(self.config_path.read_text(encoding="utf-8"))
        return MCPServerFile.model_validate(data).servers

    def load_servers(self) -> List[Dict]:
        """Return dictionary records for application-level adapters."""
        return [
            server.model_dump(mode="json")
            for server in self.load_server_configs()
        ]
