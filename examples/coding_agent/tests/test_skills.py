"""Tests for the skill system: parsing, discovery, loading, tool integration."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from app.dispatch import DispatchRegistry
from app.skills import SkillEntry, SkillRegistry, _parse_skill, register_skill_tool


# ── Parsing ──


class TestParseSkill:
    def test_full_frontmatter(self, tmp_path):
        f = tmp_path / "review.md"
        f.write_text(
            "---\n"
            "name: code-review\n"
            "description: Review code\n"
            "trigger: when asked to review\n"
            "---\n"
            "# Code Review\n"
            "Read files first.\n"
        )
        entry = _parse_skill(f)
        assert entry is not None
        assert entry.name == "code-review"
        assert entry.description == "Review code"
        assert entry.trigger == "when asked to review"
        assert "# Code Review" in entry.body
        assert "Read files first" in entry.body

    def test_no_frontmatter_uses_filename(self, tmp_path):
        f = tmp_path / "my-skill.md"
        f.write_text("# Just a body\nNo frontmatter here.\n")
        entry = _parse_skill(f)
        assert entry is not None
        assert entry.name == "my-skill"
        assert entry.description == ""
        assert "Just a body" in entry.body

    def test_invalid_yaml_returns_none(self, tmp_path):
        f = tmp_path / "bad.md"
        f.write_text("---\n: invalid yaml [\n---\nBody.\n")
        entry = _parse_skill(f)
        assert entry is None

    def test_incomplete_frontmatter(self, tmp_path):
        f = tmp_path / "partial.md"
        f.write_text("---\nname: partial\n")  # no closing ---
        entry = _parse_skill(f)
        assert entry is None

    def test_missing_fields_use_defaults(self, tmp_path):
        f = tmp_path / "minimal.md"
        f.write_text("---\nname: x\n---\nBody text.\n")
        entry = _parse_skill(f)
        assert entry.name == "x"
        assert entry.description == ""
        assert entry.trigger == ""
        assert entry.body == "Body text."


# ── SkillRegistry ──


class TestSkillRegistry:
    def _make_skills_dir(self, tmp_path, skills):
        """Create skill files from {filename: content} dict."""
        d = tmp_path / "skills"
        d.mkdir()
        for name, content in skills.items():
            (d / name).write_text(content)
        return str(d)

    def test_loads_all_skills(self, tmp_path):
        d = self._make_skills_dir(tmp_path, {
            "a.md": "---\nname: alpha\ndescription: First\ntrigger: t1\n---\nBody A",
            "b.md": "---\nname: beta\ndescription: Second\ntrigger: t2\n---\nBody B",
        })
        reg = SkillRegistry(d)
        assert reg.list_names() == ["alpha", "beta"]

    def test_discover(self, tmp_path):
        d = self._make_skills_dir(tmp_path, {
            "a.md": "---\nname: alpha\ndescription: First skill\ntrigger: t1\n---\nBody",
            "b.md": "---\nname: beta\ndescription: Second skill\ntrigger: t2\n---\nBody",
        })
        reg = SkillRegistry(d)
        discovered = reg.discover()
        assert len(discovered) == 2
        names = [s["name"] for s in discovered]
        assert "alpha" in names and "beta" in names
        assert all("description" in s for s in discovered)

    def test_discover_text(self, tmp_path):
        d = self._make_skills_dir(tmp_path, {
            "a.md": "---\nname: alpha\ndescription: First skill\n---\nBody",
        })
        reg = SkillRegistry(d)
        text = reg.discover_text()
        assert "alpha" in text
        assert "First skill" in text

    def test_load(self, tmp_path):
        d = self._make_skills_dir(tmp_path, {
            "a.md": "---\nname: alpha\ndescription: Desc\n---\nFull instructions here.",
        })
        reg = SkillRegistry(d)
        body = reg.load("alpha")
        assert "Full instructions here." in body

    def test_load_unknown_raises(self, tmp_path):
        d = self._make_skills_dir(tmp_path, {
            "a.md": "---\nname: alpha\ndescription: Desc\n---\nBody",
        })
        reg = SkillRegistry(d)
        with pytest.raises(KeyError, match="not found"):
            reg.load("nonexistent")

    def test_load_with_meta(self, tmp_path):
        d = self._make_skills_dir(tmp_path, {
            "a.md": "---\nname: alpha\ndescription: Desc\ntrigger: when needed\n---\nBody text.",
        })
        reg = SkillRegistry(d)
        result = reg.load_with_meta("alpha")
        assert "[Skill: alpha]" in result
        assert "when needed" in result
        assert "Body text." in result

    def test_empty_directory(self, tmp_path):
        d = tmp_path / "empty_skills"
        d.mkdir()
        reg = SkillRegistry(str(d))
        assert reg.list_names() == []
        assert reg.discover() == []

    def test_nonexistent_directory(self):
        reg = SkillRegistry("/nonexistent/path")
        assert reg.list_names() == []

    def test_sorted_order(self, tmp_path):
        d = self._make_skills_dir(tmp_path, {
            "z.md": "---\nname: zebra\ndescription: Z\n---\nZ",
            "a.md": "---\nname: apple\ndescription: A\n---\nA",
            "m.md": "---\nname: mango\ndescription: M\n---\nM",
        })
        reg = SkillRegistry(d)
        assert reg.list_names() == ["apple", "mango", "zebra"]


# ── Tool integration ──


class TestSkillTool:
    def _make_setup(self, tmp_path):
        d = tmp_path / "skills"
        d.mkdir()
        (d / "review.md").write_text(
            "---\nname: code-review\ndescription: Review code\ntrigger: when reviewing\n---\n# Review\nRead first."
        )
        (d / "test.md").write_text(
            "---\nname: test-writing\ndescription: Write tests\ntrigger: when testing\n---\n# Tests\nCover edges."
        )
        skill_reg = SkillRegistry(str(d))
        tool_reg = DispatchRegistry(workspace_root=str(tmp_path))
        register_skill_tool(tool_reg, skill_reg)
        return tool_reg, skill_reg

    def test_skill_tool_registered(self, tmp_path):
        tool_reg, _ = self._make_setup(tmp_path)
        assert "skill" in tool_reg.dispatch_map

    def test_load_skill_via_tool(self, tmp_path):
        tool_reg, _ = self._make_setup(tmp_path)
        result = tool_reg.call("skill", {"name": "code-review"})
        assert "[Skill: code-review]" in result
        assert "Read first" in result

    def test_unknown_skill_error(self, tmp_path):
        tool_reg, _ = self._make_setup(tmp_path)
        with pytest.raises(RuntimeError, match="not found"):
            tool_reg.call("skill", {"name": "nonexistent"})

    def test_empty_name_error(self, tmp_path):
        tool_reg, _ = self._make_setup(tmp_path)
        with pytest.raises(RuntimeError, match="No skill name"):
            tool_reg.call("skill", {"name": ""})

    def test_discovery_in_tool_description(self, tmp_path):
        """Tool schema description lists available skills for discovery."""
        tool_reg, _ = self._make_setup(tmp_path)
        schema = tool_reg.schemas["skill"]
        desc = schema["function"]["description"]
        assert "code-review" in desc
        assert "test-writing" in desc
        assert "Review code" in desc
        assert "Write tests" in desc

    def test_all_skills_loadable(self, tmp_path):
        """Every discovered skill can be loaded."""
        tool_reg, skill_reg = self._make_setup(tmp_path)
        for name in skill_reg.list_names():
            result = tool_reg.call("skill", {"name": name})
            assert result  # non-empty


# ── Real skills directory ──


class TestRealSkills:
    def test_real_skills_load(self):
        """Skills from the actual skills/ directory load successfully."""
        skills_dir = os.path.join(os.path.dirname(__file__), "..", "skills")
        if not os.path.isdir(skills_dir):
            pytest.skip("No skills/ directory")
        reg = SkillRegistry(skills_dir)
        assert len(reg.list_names()) >= 3
        for name in reg.list_names():
            body = reg.load(name)
            assert len(body) > 50  # meaningful content
