"""Skill system: discovery and loading of reusable task instructions.

A skill is a markdown file with YAML frontmatter:

    ---
    name: code-review
    description: "Review code changes for quality"
    trigger: "when asked to review code"
    ---

    # Code Review
    ...full instructions...

Two phases:
    - Discovery (cheap): list skill names + descriptions — shown in tool description
    - Loading (expensive): read full skill body — returned as tool observation

Adding a skill = adding a .md file in skills/. No code changes needed.
"""

import yaml
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from app.dispatch import DispatchRegistry, tool_result


@dataclass
class SkillEntry:
    """A single loaded skill."""
    name: str
    description: str
    trigger: str
    body: str


class SkillRegistry:
    """Scans a directory of .md skill files and provides discovery/loading."""

    def __init__(self, skills_dir: str):
        self._skills: Dict[str, SkillEntry] = {}
        self._load_all(skills_dir)

    def _load_all(self, skills_dir: str):
        path = Path(skills_dir)
        if not path.is_dir():
            return
        for f in sorted(path.glob("*.md")):
            entry = _parse_skill(f)
            if entry:
                self._skills[entry.name] = entry

    # ── Discovery (cheap) ──

    def discover(self) -> List[Dict[str, str]]:
        """Lightweight: name + description for each skill."""
        return [{"name": s.name, "description": s.description} for s in self._skills.values()]

    def discover_text(self) -> str:
        """Formatted discovery text for tool description."""
        lines = []
        for s in self._skills.values():
            lines.append(f"- {s.name}: {s.description}")
        return "\n".join(lines)

    def list_names(self) -> List[str]:
        return list(self._skills.keys())

    # ── Loading (expensive) ──

    def load(self, name: str) -> str:
        """Load full skill body by name."""
        if name not in self._skills:
            raise KeyError(f"Skill '{name}' not found. Available: {self.list_names()}")
        return self._skills[name].body

    def load_with_meta(self, name: str) -> str:
        """Load skill body prefixed with trigger hint."""
        if name not in self._skills:
            raise KeyError(f"Skill '{name}' not found. Available: {self.list_names()}")
        s = self._skills[name]
        return f"[Skill: {s.name}]\nTrigger: {s.trigger}\n\n{s.body}"


def _parse_skill(path: Path) -> Optional[SkillEntry]:
    """Parse a skill .md file with YAML frontmatter."""
    text = path.read_text(encoding="utf-8").strip()

    if not text.startswith("---"):
        # No frontmatter — use filename as name
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
        meta = yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError:
        return None

    name = meta.get("name", path.stem)
    description = meta.get("description", "")
    trigger = meta.get("trigger", "")
    body = parts[2].strip()

    return SkillEntry(name=name, description=description, trigger=trigger, body=body)


# ── Tool registration ──


def register_skill_tool(registry: DispatchRegistry, skill_registry: SkillRegistry):
    """Register the 'skill' tool for discovery and loading.

    Discovery info is embedded in the tool description — the LLM sees
    available skills without any tool call. Loading happens on demand.
    """
    menu = skill_registry.discover_text()
    available = ", ".join(skill_registry.list_names()) or "none"

    def skill_handler(args: Dict) -> tool_result:
        name = args.get("name", "")
        if not name:
            return tool_result(
                ok=False, output="",
                error=f"No skill name provided. Available: {available}",
            )
        try:
            content = skill_registry.load_with_meta(name)
            return tool_result(ok=True, output=content)
        except KeyError as e:
            return tool_result(ok=False, output="", error=str(e))

    registry.register(
        name="skill",
        handler=skill_handler,
        schema={
            "type": "function",
            "function": {
                "name": "skill",
                "description": (
                    "Load a skill's detailed instructions for the current task.\n\n"
                    "Available skills:\n" + menu + "\n\n"
                    "Call with a skill name to load its full instructions into context."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "description": "Name of the skill to load",
                        },
                    },
                    "required": ["name"],
                },
            },
        },
        path_params=[],
    )
