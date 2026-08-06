"""Discoverable extension catalog used by HarnessBuilder."""

from typing import Any, Callable, Dict, List

from nanoharness.extensions.base import ExtensionManifest, ExtensionProtocol


ExtensionFactory = Callable[[], ExtensionProtocol]


class UnknownExtensionError(KeyError):
    pass


class DuplicateCatalogEntryError(ValueError):
    pass


class ExtensionCatalog:
    def __init__(self):
        self._factories: Dict[str, ExtensionFactory] = {}
        self._manifests: Dict[str, ExtensionManifest] = {}

    def register(
        self,
        factory: ExtensionFactory,
        *,
        replace: bool = False,
    ) -> None:
        extension = factory()
        name = extension.manifest.name
        if name in self._factories and not replace:
            raise DuplicateCatalogEntryError(
                f"Extension catalog already contains {name!r}"
            )
        self._factories[name] = factory
        self._manifests[name] = extension.manifest.model_copy(deep=True)

    def create(self, name: str) -> ExtensionProtocol:
        try:
            return self._factories[name]()
        except KeyError as error:
            raise UnknownExtensionError(
                f"Unknown extension {name!r}; available: {self.names()}"
            ) from error

    def manifest(self, name: str) -> ExtensionManifest:
        try:
            return self._manifests[name].model_copy(deep=True)
        except KeyError as error:
            raise UnknownExtensionError(
                f"Unknown extension {name!r}; available: {self.names()}"
            ) from error

    def names(self) -> List[str]:
        return sorted(self._factories)

    def manifests(self) -> List[ExtensionManifest]:
        return [self.manifest(name) for name in self.names()]

    def descriptions(self) -> List[Dict[str, Any]]:
        return [self.create(name).describe() for name in self.names()]

    @classmethod
    def builtins(cls) -> "ExtensionCatalog":
        from nanoharness.extensions.background import BackgroundExtension
        from nanoharness.extensions.memory import MemoryExtension
        from nanoharness.extensions.mcp import MCPExtension
        from nanoharness.extensions.scheduler import SchedulerExtension
        from nanoharness.extensions.skills import SkillsExtension
        from nanoharness.extensions.subagents import SubagentExtension
        from nanoharness.extensions.tasks import TaskExtension
        from nanoharness.extensions.teams import TeamExtension
        from nanoharness.extensions.worktrees import WorktreeExtension

        catalog = cls()
        for factory in (
            MemoryExtension,
            SkillsExtension,
            MCPExtension,
            BackgroundExtension,
            SchedulerExtension,
            TaskExtension,
            WorktreeExtension,
            TeamExtension,
            SubagentExtension,
        ):
            catalog.register(factory)
        return catalog
