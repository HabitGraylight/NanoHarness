from pathlib import Path
from typing import Dict, Optional

import yaml


class PromptManager:
    """Central registry for all prompt templates.

    Loads from a YAML file on first access, with lazy evaluation.
    Templates use Python str.format() for variable substitution.

    Usage:
        pm = PromptManager()                       # loads configs/prompts.yaml
        pm = PromptManager.from_file("my.yaml")    # custom file
        text = pm.render("memory.inject", entries="...")
    """

    _DEFAULT_PATH = Path(__file__).resolve().parent.parent.parent / "configs" / "prompts.yaml"

    def __init__(self, templates: Optional[Dict[str, str]] = None):
        self._templates: Dict[str, str] = {}
        if templates:
            self._templates.update(templates)
        # Auto-load defaults if available
        if self._DEFAULT_PATH.exists():
            self._load_file(self._DEFAULT_PATH)

    @classmethod
    def from_file(cls, path: str) -> "PromptManager":
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
