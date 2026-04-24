"""Five-segment system prompt builder.

Segments (in order):
  1. Core identity and behavior (from prompts.yaml template)
  2. (Tools are passed via OpenAI `tools` parameter, not in system prompt)
  3. Skills metadata (from SkillRegistry)
  4. Memory content (from FileMemoryManager)
  5. NanoCA.md project instructions (from file)
  6. Dynamic environment info (runtime: cwd, OS, git, date)

Static segments (1, 3) are rendered once at construction.
Dynamic segments (4, 5, 6) are recomputed on each build() call.
"""

import datetime
import os
import subprocess
from pathlib import Path
from typing import List

from nanoharness.core.prompt import PromptManager


# ── Segment renderers ──


def render_core_identity(prompts: PromptManager) -> str:
    """Segment 1: core identity from prompts.yaml template."""
    return prompts.get("segment.core_identity")


def render_skills_metadata(skill_registry) -> str:
    """Segment 3: skill names and descriptions."""
    entries = skill_registry.discover()
    if not entries:
        return ""
    lines = ["## Skills", ""]
    for s in entries:
        lines.append(f"- **{s['name']}**: {s['description']}")
    lines.append("")
    lines.append("Use the `skill` tool to load a skill's full instructions.")
    return "\n".join(lines)


def render_memory_content(memory) -> str:
    """Segment 4: memory index from file-based memory system."""
    index = memory.load_for_injection()
    if not index:
        return ""
    return f"## Memory\n\n{index}"


def render_nano_ca_md(workspace_root: str) -> str:
    """Segment 5: project-specific instructions from NanoCA.md."""
    candidate = Path(workspace_root) / "NanoCA.md"
    if candidate.exists():
        content = candidate.read_text(encoding="utf-8").strip()
        if content:
            return f"## Project Instructions (NanoCA.md)\n\n{content}"
    return ""


def render_dynamic_env() -> str:
    """Segment 6: runtime environment info."""
    lines = ["## Environment", ""]
    lines.append(f"- Date: {datetime.date.today().isoformat()}")
    lines.append(f"- CWD: {os.getcwd()}")
    lines.append(f"- OS: {os.name}")

    # Git branch (best-effort)
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            lines.append(f"- Git branch: {result.stdout.strip()}")
    except Exception:
        pass

    return "\n".join(lines)


# ── Builder ──


class SystemPromptBuilder:
    """Assembles the five-segment system prompt.

    Usage:
        builder = SystemPromptBuilder(prompts, skill_registry, memory, workspace_root)
        system_prompt = builder.build()
    """

    def __init__(self, prompts, skill_registry, memory, workspace_root: str):
        self._prompts = prompts
        self._memory = memory
        self._workspace_root = workspace_root

        # Pre-render static segments
        self._static = "\n\n".join(filter(None, [
            render_core_identity(prompts),
            render_skills_metadata(skill_registry),
        ]))

    def build(self) -> str:
        """Build the full system prompt with current dynamic segments."""
        dynamic = "\n\n".join(filter(None, [
            render_memory_content(self._memory),
            render_nano_ca_md(self._workspace_root),
            render_dynamic_env(),
        ]))
        if dynamic:
            return f"{self._static}\n\n{dynamic}"
        return self._static
