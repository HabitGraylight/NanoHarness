"""ST tests for the skill system: tool integration and real skills loading."""

import pytest

from nanoharness.components.tools import DictToolRegistry
from nanoharness.extensions.skills import SkillRegistry, register_skill_tool


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
        tool_reg = DictToolRegistry()
        register_skill_tool(tool_reg, skill_reg)
        return tool_reg, skill_reg

    def test_skill_tool_registered(self, tmp_path):
        tool_reg, _ = self._make_setup(tmp_path)
        names = {
            schema["function"]["name"] for schema in tool_reg.get_tool_schemas()
        }
        assert "skill" in names

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
        schema = next(
            schema for schema in tool_reg.get_tool_schemas()
            if schema["function"]["name"] == "skill"
        )
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
