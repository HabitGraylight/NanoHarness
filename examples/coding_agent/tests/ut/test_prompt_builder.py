"""Tests for the five-segment system prompt builder."""

import os
import pytest

from nanoharness.core.prompt import PromptManager
from app.prompt_builder import (
    SystemPromptBuilder,
    render_core_identity,
    render_skills_metadata,
    render_memory_content,
    render_nano_ca_md,
    render_dynamic_env,
)


# ── Fixtures ──


@pytest.fixture
def prompts():
    path = os.path.join(os.path.dirname(__file__), "..", "..", "app", "prompts.yaml")
    return PromptManager.from_file(path)


@pytest.fixture
def workspace_root():
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


class FakeSkillRegistry:
    def __init__(self, entries=None):
        self._entries = entries or []

    def discover(self):
        return self._entries


class FakeMemory:
    def __init__(self, index=""):
        self._index = index

    def load_for_injection(self):
        return self._index


# ── Segment 1: core identity ──


def test_core_identity_renders(prompts):
    result = render_core_identity(prompts)
    assert "software engineer" in result.lower()
    assert len(result) > 50


# ── Segment 3: skills metadata ──


def test_skills_metadata_with_entries():
    reg = FakeSkillRegistry([
        {"name": "code-review", "description": "Review code changes"},
        {"name": "debugging", "description": "Debug issues"},
    ])
    result = render_skills_metadata(reg)
    assert "code-review" in result
    assert "debugging" in result
    assert "skill" in result.lower()


def test_skills_metadata_empty():
    reg = FakeSkillRegistry([])
    result = render_skills_metadata(reg)
    assert result == ""


# ── Segment 4: memory content ──


def test_memory_with_content():
    mem = FakeMemory(index="- [Pref](pref.md) — user prefers tabs")
    result = render_memory_content(mem)
    assert "## Memory" in result
    assert "Pref" in result


def test_memory_empty():
    mem = FakeMemory(index="")
    result = render_memory_content(mem)
    assert result == ""


# ── Segment 5: NanoCA.md ──


def test_nano_ca_md_present(tmp_path):
    nano_ca = tmp_path / "NanoCA.md"
    nano_ca.write_text("# Rules\nUse type hints always.")
    result = render_nano_ca_md(str(tmp_path))
    assert "## Project Instructions" in result
    assert "type hints" in result


def test_nano_ca_md_missing(tmp_path):
    result = render_nano_ca_md(str(tmp_path))
    assert result == ""


def test_nano_ca_md_empty(tmp_path):
    nano_ca = tmp_path / "NanoCA.md"
    nano_ca.write_text("   \n  \n")
    result = render_nano_ca_md(str(tmp_path))
    assert result == ""


# ── Segment 6: dynamic environment ──


def test_dynamic_env_includes_date():
    result = render_dynamic_env()
    assert "Date:" in result
    assert "CWD:" in result
    assert "## Environment" in result


# ── Full builder ──


def test_builder_produces_single_string(prompts, workspace_root):
    builder = SystemPromptBuilder(
        prompts=prompts,
        skill_registry=FakeSkillRegistry([{"name": "test", "description": "A test skill"}]),
        memory=FakeMemory(index="some memory"),
        workspace_root=workspace_root,
    )
    result = builder.build()
    assert isinstance(result, str)
    assert "software engineer" in result.lower()
    assert "test" in result
    assert "some memory" in result
    assert "NanoCA.md" in result
    assert "Environment" in result


def test_builder_dynamic_refresh(prompts, workspace_root):
    """Two build() calls produce different output when memory changes."""
    memory = FakeMemory(index="first memory")
    builder = SystemPromptBuilder(
        prompts=prompts,
        skill_registry=FakeSkillRegistry(),
        memory=memory,
        workspace_root=workspace_root,
    )
    first = builder.build()
    memory._index = "second memory"
    second = builder.build()
    assert "first memory" in first
    assert "second memory" in second
    assert first != second


def test_builder_no_nano_ca_md(prompts, tmp_path):
    """Builder handles missing NanoCA.md gracefully."""
    builder = SystemPromptBuilder(
        prompts=prompts,
        skill_registry=FakeSkillRegistry(),
        memory=FakeMemory(),
        workspace_root=str(tmp_path),
    )
    result = builder.build()
    assert "NanoCA.md" not in result
