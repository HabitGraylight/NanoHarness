"""White-box contracts shared by reusable NanoHarness extensions."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Mapping, MutableMapping, Optional, Protocol, Set, Type

from pydantic import BaseModel, ConfigDict, Field

from nanoharness.core.base import BaseToolRegistry


EXTENSION_PROTOCOL_VERSION = "1.0"


class ExtensionManifest(BaseModel):
    """Static capability declaration used before an extension is installed."""

    name: str
    version: str
    description: str = ""
    provides: List[str] = Field(default_factory=list)
    requires: List[str] = Field(default_factory=list)
    conflicts: List[str] = Field(default_factory=list)
    protocol_version: str = EXTENSION_PROTOCOL_VERSION


class ExtensionInstallation(BaseModel):
    """Inspectable receipt produced by a successful installation."""

    name: str
    version: str
    capabilities: List[str] = Field(default_factory=list)
    requires: List[str] = Field(default_factory=list)
    conflicts: List[str] = Field(default_factory=list)
    tools: List[str] = Field(default_factory=list)
    services: List[str] = Field(default_factory=list)
    config: Dict[str, Any] = Field(default_factory=dict)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class EmptyExtensionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")


@dataclass
class ExtensionContext:
    """Mutable installation surface intentionally visible to applications."""

    tools: BaseToolRegistry
    services: MutableMapping[str, Any] = field(default_factory=dict)
    capabilities: Set[str] = field(default_factory=set)
    metadata: MutableMapping[str, Any] = field(default_factory=dict)

    def provide_service(self, name: str, service: Any, *, replace: bool = False) -> None:
        if name in self.services and not replace:
            raise ValueError(f"Service '{name}' is already registered")
        self.services[name] = service

    def tool_names(self) -> Set[str]:
        return {
            schema["function"]["name"]
            for schema in self.tools.get_tool_schemas()
        }

    def register_tool(
        self,
        name: str,
        handler: Callable[[Dict[str, Any]], Any],
        schema: Dict[str, Any],
        *,
        replace: bool = False,
    ) -> None:
        if name in self.tool_names() and not replace:
            raise ValueError(f"Tool '{name}' is already registered")
        register = getattr(self.tools, "register", None)
        if register is None:
            raise TypeError("Extension tools require a schema-first registry.register()")
        register(name=name, handler=handler, schema=schema)

    def register(
        self,
        name: str,
        handler: Callable[[Dict[str, Any]], Any],
        schema: Dict[str, Any],
    ) -> None:
        """Registry-compatible alias used by portable tool installers."""
        self.register_tool(name, handler, schema)


class ExtensionProtocol(Protocol):
    manifest: ExtensionManifest
    config_model: Type[BaseModel]

    def parse_config(
        self,
        config: Optional[Mapping[str, Any] | BaseModel] = None,
    ) -> BaseModel:
        ...

    def config_schema(self) -> Dict[str, Any]:
        ...

    def install(
        self,
        context: ExtensionContext,
        config: BaseModel,
    ) -> ExtensionInstallation:
        ...

    def close(
        self,
        context: ExtensionContext,
        installation: ExtensionInstallation,
    ) -> None:
        ...


class NotificationSourceProtocol(Protocol):
    """Host-facing source of system notifications from long-lived services."""

    def drain(self) -> List[Dict[str, Any]]:
        ...


class BaseExtension(ABC):
    """Convenience base that standardizes config validation and inspection."""

    manifest: ExtensionManifest
    config_model: Type[BaseModel] = EmptyExtensionConfig

    def parse_config(
        self,
        config: Optional[Mapping[str, Any] | BaseModel] = None,
    ) -> BaseModel:
        if isinstance(config, self.config_model):
            return config
        if isinstance(config, BaseModel):
            config = config.model_dump()
        return self.config_model.model_validate(config or {})

    def config_schema(self) -> Dict[str, Any]:
        return self.config_model.model_json_schema()

    def describe(self) -> Dict[str, Any]:
        return {
            "manifest": self.manifest.model_dump(mode="json"),
            "config_schema": self.config_schema(),
        }

    def close(
        self,
        context: ExtensionContext,
        installation: ExtensionInstallation,
    ) -> None:
        """Release resources owned by an installed extension.

        Most extensions are passive and need no cleanup. Resource-owning
        extensions can override this hook; ExtensionManager calls it once in
        reverse installation order.
        """

    @abstractmethod
    def install(
        self,
        context: ExtensionContext,
        config: BaseModel,
    ) -> ExtensionInstallation:
        pass
