"""Portable discovery and loading for Markdown instruction skills."""

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

import yaml


@dataclass
class SkillEntry:
    name: str
    description: str
    trigger: str
    body: str


def parse_skill(path: Path) -> Optional[SkillEntry]:
    """Parse one Markdown skill with optional YAML frontmatter."""
    text = path.read_text(encoding="utf-8").strip()
    if not text.startswith("---"):
        return SkillEntry(
            name=path.stem,
            description="",
            trigger="",
            body=text,
        )

    parts = text.split("---", 2)
    if len(parts) < 3:
        return None
    try:
        metadata = yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError:
        return None

    return SkillEntry(
        name=metadata.get("name", path.stem),
        description=metadata.get("description", ""),
        trigger=metadata.get("trigger", ""),
        body=parts[2].strip(),
    )


class SkillRegistry:
    """Discover lightweight metadata and load full skill bodies on demand."""

    def __init__(self, skills_dir: str, pattern: str = "*.md"):
        self.directory = Path(skills_dir)
        self.pattern = pattern
        self._skills: Dict[str, SkillEntry] = {}
        self.reload()

    def reload(self) -> None:
        self._skills.clear()
        if not self.directory.is_dir():
            return
        for path in sorted(self.directory.glob(self.pattern)):
            entry = parse_skill(path)
            if entry:
                self._skills[entry.name] = entry

    def discover(self) -> List[Dict[str, str]]:
        return [
            {"name": skill.name, "description": skill.description}
            for skill in self._skills.values()
        ]

    def discover_text(self) -> str:
        return "\n".join(
            f"- {skill.name}: {skill.description}"
            for skill in self._skills.values()
        )

    def list_names(self) -> List[str]:
        return list(self._skills)

    def load(self, name: str) -> str:
        if name not in self._skills:
            raise KeyError(f"Skill '{name}' not found. Available: {self.list_names()}")
        return self._skills[name].body

    def load_with_meta(self, name: str) -> str:
        if name not in self._skills:
            raise KeyError(f"Skill '{name}' not found. Available: {self.list_names()}")
        skill = self._skills[name]
        return f"[Skill: {skill.name}]\nTrigger: {skill.trigger}\n\n{skill.body}"


# Private alias used by application-level skill loaders.
_parse_skill = parse_skill
