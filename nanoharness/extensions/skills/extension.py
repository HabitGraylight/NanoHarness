"""Reusable directory-backed Skills extension."""

from typing import Any, Dict

from pydantic import BaseModel, ConfigDict

from nanoharness.extensions.base import (
    BaseExtension,
    ExtensionContext,
    ExtensionInstallation,
    ExtensionManifest,
)
from nanoharness.extensions.skills.registry import SkillRegistry


class SkillsExtensionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    directory: str = "skills"
    pattern: str = "*.md"
    service_name: str = "skills"
    tool_name: str = "skill"


def register_skill_tool(registry, skill_registry: SkillRegistry, *, tool_name="skill") -> str:
    """Register on a registry or ExtensionContext using the common surface."""
    menu = skill_registry.discover_text()
    available = ", ".join(skill_registry.list_names()) or "none"

    def skill_handler(args: Dict[str, Any]) -> str:
        name = args.get("name", "")
        if not name:
            raise RuntimeError(f"No skill name provided. Available: {available}")
        try:
            return skill_registry.load_with_meta(name)
        except KeyError as exc:
            raise RuntimeError(str(exc)) from exc

    registry.register(
        name=tool_name,
        handler=skill_handler,
        schema={
            "type": "function",
            "function": {
                "name": tool_name,
                "description": (
                    "Load a skill's detailed instructions for the current task.\n\n"
                    "Available skills:\n"
                    + menu
                    + "\n\nCall with a skill name to load its full instructions."
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
    )
    return tool_name


class SkillsExtension(BaseExtension):
    manifest = ExtensionManifest(
        name="skills.directory",
        version="1.0.0",
        description="Discover Markdown skills and load instructions on demand.",
        provides=["skills.registry", "tools.skills"],
    )
    config_model = SkillsExtensionConfig

    def install(
        self,
        context: ExtensionContext,
        config: BaseModel,
    ) -> ExtensionInstallation:
        if not isinstance(config, SkillsExtensionConfig):
            raise TypeError("SkillsExtension requires SkillsExtensionConfig")
        if config.tool_name in context.tool_names():
            raise ValueError(f"SkillsExtension tool conflicts: {config.tool_name!r}")
        if config.service_name in context.services:
            raise ValueError(
                f"SkillsExtension service conflicts: {config.service_name!r}"
            )

        skills = SkillRegistry(config.directory, pattern=config.pattern)
        tool_name = register_skill_tool(
            context,
            skills,
            tool_name=config.tool_name,
        )
        context.provide_service(config.service_name, skills)
        return ExtensionInstallation(
            name=self.manifest.name,
            version=self.manifest.version,
            capabilities=list(self.manifest.provides),
            tools=[tool_name],
            services=[config.service_name],
            config=config.model_dump(mode="json"),
            metadata={
                "directory": str(skills.directory),
                "skill_count": len(skills.list_names()),
                "skills": skills.list_names(),
            },
        )
