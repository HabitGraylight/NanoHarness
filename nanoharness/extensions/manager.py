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


class ExtensionManagerClosedError(RuntimeError):
    pass


class ExtensionShutdownError(RuntimeError):
    def __init__(self, failures: Dict[str, Exception]):
        self.failures = failures
        details = ", ".join(f"{name}: {error}" for name, error in failures.items())
        super().__init__(f"Failed to close extensions: {details}")


class ExtensionManager:
    """Install extensions in dependency order and expose the resolved graph."""

    def __init__(self, context: ExtensionContext):
        self.context = context
        self._installations: Dict[str, ExtensionInstallation] = {}
        self._extensions: Dict[str, ExtensionProtocol] = {}
        self._closed = False

    @property
    def installations(self) -> Dict[str, ExtensionInstallation]:
        return dict(self._installations)

    @property
    def closed(self) -> bool:
        return self._closed

    def install(
        self,
        extension: ExtensionProtocol,
        config: Optional[Mapping[str, Any] | BaseModel] = None,
    ) -> ExtensionInstallation:
        if self._closed:
            raise ExtensionManagerClosedError(
                "Cannot install extensions after ExtensionManager.close()"
            )
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
        self._extensions[manifest.name] = extension
        return installation

    def close(self) -> None:
        """Close installed extensions once, in reverse installation order."""
        if self._closed:
            return
        self._closed = True
        failures: Dict[str, Exception] = {}
        for name in reversed(self._installations):
            extension = self._extensions[name]
            close = getattr(extension, "close", None)
            if close is None:
                continue
            try:
                close(self.context, self._installations[name])
            except Exception as error:  # cleanup should continue for other extensions
                failures[name] = error
        if failures:
            raise ExtensionShutdownError(failures)

    def __enter__(self) -> "ExtensionManager":
        if self._closed:
            raise ExtensionManagerClosedError("ExtensionManager is already closed")
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> bool:
        try:
            self.close()
        except ExtensionShutdownError:
            if exc_type is None:
                raise
        return False

    def inspect(self) -> Dict[str, Any]:
        """Return a serialization-friendly resolved extension inventory."""
        return {
            "capabilities": sorted(self.context.capabilities),
            "extensions": [
                installation.model_dump(mode="json")
                for installation in self._installations.values()
            ],
            "services": sorted(self.context.services),
            "closed": self._closed,
        }
