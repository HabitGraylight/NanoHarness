"""Dependency-aware installer and white-box inventory for extensions."""

from typing import Any, Dict, Mapping, Optional

from pydantic import BaseModel

from nanoharness.extensions.base import (
    ExtensionContext,
    ExtensionInstallation,
    ExtensionProtocol,
)


class ExtensionDependencyError(RuntimeError):
    pass


class ExtensionConflictError(RuntimeError):
    pass


class DuplicateExtensionError(RuntimeError):
    pass


class ExtensionManager:
    """Install extensions in dependency order and expose the resolved graph."""

    def __init__(self, context: ExtensionContext):
        self.context = context
        self._installations: Dict[str, ExtensionInstallation] = {}

    @property
    def installations(self) -> Dict[str, ExtensionInstallation]:
        return dict(self._installations)

    def install(
        self,
        extension: ExtensionProtocol,
        config: Optional[Mapping[str, Any] | BaseModel] = None,
    ) -> ExtensionInstallation:
        manifest = extension.manifest
        if manifest.name in self._installations:
            raise DuplicateExtensionError(
                f"Extension '{manifest.name}' is already installed"
            )

        missing = sorted(set(manifest.requires) - self.context.capabilities)
        if missing:
            raise ExtensionDependencyError(
                f"Extension '{manifest.name}' is missing capabilities: {missing}"
            )

        conflicts = sorted(set(manifest.conflicts) & self.context.capabilities)
        if conflicts:
            raise ExtensionConflictError(
                f"Extension '{manifest.name}' conflicts with capabilities: {conflicts}"
            )

        parsed = extension.parse_config(config)
        installation = extension.install(self.context, parsed)
        if installation.name != manifest.name:
            raise ValueError(
                "Installation name does not match extension manifest: "
                f"{installation.name!r} != {manifest.name!r}"
            )

        declared = set(manifest.provides)
        installed = set(installation.capabilities)
        if declared != installed:
            raise ValueError(
                f"Extension '{manifest.name}' installed capabilities {sorted(installed)} "
                f"but declared {sorted(declared)}"
            )

        self.context.capabilities.update(installed)
        self._installations[manifest.name] = installation
        return installation

    def inspect(self) -> Dict[str, Any]:
        """Return a serialization-friendly resolved extension inventory."""
        return {
            "capabilities": sorted(self.context.capabilities),
            "extensions": [
                installation.model_dump(mode="json")
                for installation in self._installations.values()
            ],
            "services": sorted(self.context.services),
        }
