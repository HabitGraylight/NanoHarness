"""Compatibility imports for the reusable NanoHarness Skills extension."""

from nanoharness.extensions.skills import (
    SkillEntry,
    SkillRegistry,
    _parse_skill,
    parse_skill,
    register_skill_tool,
)

__all__ = [
    "SkillEntry",
    "SkillRegistry",
    "_parse_skill",
    "parse_skill",
    "register_skill_tool",
]
