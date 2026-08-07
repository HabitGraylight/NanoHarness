"""NanoClaudeCode owns and validates its concrete skill assets."""

from pathlib import Path

from nanoharness.extensions.skills import SkillRegistry


def test_real_skills_load():
    skills_dir = Path(__file__).resolve().parents[2] / "skills"
    registry = SkillRegistry(str(skills_dir))
    assert len(registry.list_names()) >= 3
    for name in registry.list_names():
        assert len(registry.load(name)) > 50
