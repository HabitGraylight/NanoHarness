"""Validated configuration models for stdio MCP servers."""

import json
from pathlib import Path
from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class MCPServerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    command: str = Field(min_length=1)
    args: List[str] = Field(default_factory=list)
    transport: Literal["stdio"] = "stdio"
    env: Dict[str, str] = Field(default_factory=dict)
    cwd: Optional[str] = None


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
