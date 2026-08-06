"""Reusable MCP stdio extension with dynamic schema-first tools."""

import warnings
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional

from pydantic import BaseModel, ConfigDict, Field

from nanoharness.extensions.base import (
    BaseExtension,
    ExtensionContext,
    ExtensionInstallation,
    ExtensionManifest,
)
from nanoharness.extensions.mcp.client import MCPClient
from nanoharness.extensions.mcp.models import MCPServerConfig, PluginLoader


class MCPExtensionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    config_path: Optional[str] = None
    servers: List[MCPServerConfig] = Field(default_factory=list)
    service_name: str = "mcp"
    tool_namespace: str = "mcp"
    fail_fast: bool = False
    read_timeout_seconds: float = Field(default=15.0, gt=0, le=300)
    client_name: str = "nanoharness"
    client_version: str = "0.1.0"


@dataclass
class MCPClientPool:
    clients: List[MCPClient] = field(default_factory=list)
    failures: Dict[str, str] = field(default_factory=dict)
    tool_names: List[str] = field(default_factory=list)

    @property
    def connected_names(self) -> List[str]:
        return [client.name for client in self.clients if client.connected]

    def close(self) -> None:
        errors = []
        for client in reversed(self.clients):
            try:
                client.disconnect()
            except Exception as error:
                errors.append(f"{client.name}: {error}")
        if errors:
            raise RuntimeError("; ".join(errors))


def mcp_tool_name(namespace: str, server_name: str, tool_name: str) -> str:
    prefix = namespace.rstrip("_")
    if prefix:
        return f"{prefix}__{server_name}__{tool_name}"
    return f"{server_name}__{tool_name}"


def mcp_handler(client: MCPClient, tool_name: str):
    """Create a portable handler that returns text and raises on failure."""
    def handler(args: Dict[str, Any]) -> str:
        return client.call_tool(tool_name, args)
    return handler


def _validate_server_names(servers: Iterable[MCPServerConfig]) -> None:
    seen = set()
    duplicates = set()
    for server in servers:
        if server.name in seen:
            duplicates.add(server.name)
        seen.add(server.name)
    if duplicates:
        raise ValueError(f"Duplicate MCP server names: {sorted(duplicates)}")


def _connect_and_discover(
    servers: Iterable[MCPServerConfig],
    *,
    fail_fast: bool,
    read_timeout_seconds: float,
    client_name: str,
    client_version: str,
) -> tuple[MCPClientPool, List[tuple[MCPClient, Dict[str, Any]]]]:
    pool = MCPClientPool()
    discovered: List[tuple[MCPClient, Dict[str, Any]]] = []
    for server in servers:
        client = MCPClient(
            name=server.name,
            command=server.command,
            args=server.args,
            env=server.env,
            cwd=server.cwd,
            read_timeout_seconds=read_timeout_seconds,
            client_name=client_name,
            client_version=client_version,
        )
        try:
            client.connect()
            tools = client.list_tools()
        except Exception as error:
            client.disconnect()
            pool.failures[server.name] = str(error)
            if fail_fast:
                pool.close()
                raise RuntimeError(
                    f"MCP server '{server.name}' failed: {error}"
                ) from error
            warnings.warn(
                f"MCP server '{server.name}' failed: {error}",
                RuntimeWarning,
                stacklevel=3,
            )
            continue
        pool.clients.append(client)
        discovered.extend((client, tool) for tool in tools)
    return pool, discovered


def install_mcp_tools(
    registry,
    servers: Iterable[MCPServerConfig | Mapping[str, Any]],
    *,
    tool_namespace: str = "mcp",
    fail_fast: bool = False,
    read_timeout_seconds: float = 15.0,
    client_name: str = "nanoharness",
    client_version: str = "0.1.0",
) -> MCPClientPool:
    """Connect servers, preflight dynamic names, then register their tools."""
    parsed_servers = [
        server
        if isinstance(server, MCPServerConfig)
        else MCPServerConfig.model_validate(server)
        for server in servers
    ]
    _validate_server_names(parsed_servers)
    pool, discovered = _connect_and_discover(
        parsed_servers,
        fail_fast=fail_fast,
        read_timeout_seconds=read_timeout_seconds,
        client_name=client_name,
        client_version=client_version,
    )
    registrations: List[tuple[str, MCPClient, Dict[str, Any]]] = []
    for client, tool in discovered:
        remote_name = tool.get("name", "")
        if not remote_name:
            continue
        registrations.append(
            (
                mcp_tool_name(tool_namespace, client.name, remote_name),
                client,
                tool,
            )
        )

    names = [name for name, _, _ in registrations]
    duplicate_tools = sorted({name for name in names if names.count(name) > 1})
    if hasattr(registry, "tool_names"):
        existing = set(registry.tool_names())
    else:
        existing = {
            schema["function"]["name"]
            for schema in registry.get_tool_schemas()
        }
    conflicts = sorted(set(names) & existing)
    if duplicate_tools or conflicts:
        pool.close()
        details = []
        if duplicate_tools:
            details.append(f"duplicate discovered tools: {duplicate_tools}")
        if conflicts:
            details.append(f"tool conflicts: {conflicts}")
        raise ValueError("MCP extension " + "; ".join(details))

    for name, client, tool in registrations:
        remote_name = tool["name"]
        registry.register(
            name=name,
            handler=mcp_handler(client, remote_name),
            schema={
                "type": "function",
                "function": {
                    "name": name,
                    "description": tool.get("description", ""),
                    "parameters": tool.get(
                        "inputSchema",
                        {"type": "object", "properties": {}},
                    ),
                },
            },
        )
    pool.tool_names = names
    return pool


def register_mcp_tools(registry, config_path: str) -> List[MCPClient]:
    """Install configured MCP tools; callers must disconnect the clients."""
    pool = install_mcp_tools(
        registry,
        PluginLoader(config_path).load_server_configs(),
    )
    return pool.clients


def _redacted_config(config: MCPExtensionConfig) -> Dict[str, Any]:
    receipt = config.model_dump(mode="json")
    for server in receipt["servers"]:
        server["env"] = {key: "***" for key in server["env"]}
    return receipt


class MCPExtension(BaseExtension):
    manifest = ExtensionManifest(
        name="mcp.stdio",
        version="1.0.0",
        description="External MCP servers over stdio with dynamic tool discovery.",
        provides=["mcp.clients", "tools.mcp"],
    )
    config_model = MCPExtensionConfig

    def install(
        self,
        context: ExtensionContext,
        config: BaseModel,
    ) -> ExtensionInstallation:
        if not isinstance(config, MCPExtensionConfig):
            raise TypeError("MCPExtension requires MCPExtensionConfig")
        if config.service_name in context.services:
            raise ValueError(f"MCPExtension service conflicts: {config.service_name!r}")

        servers = list(config.servers)
        if config.config_path:
            servers.extend(PluginLoader(config.config_path).load_server_configs())
        _validate_server_names(servers)
        pool = install_mcp_tools(
            context,
            servers,
            tool_namespace=config.tool_namespace,
            fail_fast=config.fail_fast,
            read_timeout_seconds=config.read_timeout_seconds,
            client_name=config.client_name,
            client_version=config.client_version,
        )
        context.provide_service(config.service_name, pool)
        tool_names = sorted(pool.tool_names)
        return ExtensionInstallation(
            name=self.manifest.name,
            version=self.manifest.version,
            capabilities=list(self.manifest.provides),
            tools=tool_names,
            services=[config.service_name],
            config=_redacted_config(config),
            metadata={
                "configured_servers": [server.name for server in servers],
                "connected_servers": pool.connected_names,
                "failed_servers": sorted(pool.failures),
                "tool_count": len(tool_names),
                "transport": "stdio",
                "optional_dependency": "mcp>=1.0",
            },
        )

    def close(
        self,
        context: ExtensionContext,
        installation: ExtensionInstallation,
    ) -> None:
        for service_name in installation.services:
            service = context.services.get(service_name)
            if isinstance(service, MCPClientPool):
                service.close()
