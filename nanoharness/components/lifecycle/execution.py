"""Default tool execution adapter."""

from nanoharness.core.base import BaseToolRegistry
from nanoharness.core.schema import ToolRequest


class RegistryToolExecutor:
    """Execute through a registry; replace this boundary with a sandbox."""

    def __init__(self, registry: BaseToolRegistry):
        self._registry = registry

    def execute(self, request: ToolRequest):
        return self._registry.call(request.name, request.arguments)
