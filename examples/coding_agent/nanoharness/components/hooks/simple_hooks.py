from collections import defaultdict
from typing import Any, Callable, Dict, List

from nanoharness.core.base import BaseHookManager


class SimpleHookManager(BaseHookManager):
    """Flexible lifecycle hook manager — any stage string can be registered."""

    def __init__(self):
        self._hooks: Dict[str, List[Callable]] = defaultdict(list)

    def register(self, stage: str, hook: Callable):
        self._hooks[stage].append(hook)

    def trigger(self, stage: str, data: Any):
        for hook in self._hooks.get(stage, []):
            hook(data)

    def reset(self):
        self._hooks.clear()
