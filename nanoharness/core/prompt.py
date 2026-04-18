from pathlib import Path
from typing import Dict

import yaml


class PromptManager:
    """Central registry for prompt templates.

    Templates use Python str.format() for variable substitution.

    Usage:
        pm = PromptManager.from_file("configs/prompts.yaml")
        text = pm.render("memory.inject", entries="...")
    """

    def __init__(self):
        self._templates: Dict[str, str] = {}

    @classmethod
    def from_file(cls, path: str) -> "PromptManager":
        """Load templates from a YAML file."""
        instance = cls()
        instance._load_file(Path(path))
        return instance

    def get(self, name: str) -> str:
        """Get a raw template string by name."""
        return self._templates[name]

    def render(self, name: str, **kwargs) -> str:
        """Get a template and substitute variables."""
        template = self._templates[name]
        return template.format(**kwargs).strip()

    def add(self, name: str, template: str):
        """Register or override a template at runtime."""
        self._templates[name] = template

    def keys(self):
        return self._templates.keys()

    def _load_file(self, path: Path):
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        if isinstance(data, dict):
            self._templates.update(data)
