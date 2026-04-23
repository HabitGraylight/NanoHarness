"""File-based memory system with .memory/ directory.

Structure:
    .memory/
      MEMORY.md           # Auto-generated index (one line per entry)
      prefer_tabs.md      # Individual topic file
      feedback_tests.md   # Another topic file
      ...

Each .md file has optional YAML frontmatter (name, description, type).
MEMORY.md is an index listing all entries — always loaded into context.
"""

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional


@dataclass
class MemoryEntry:
    """A single memory entry read from a .md file."""
    name: str
    filename: str
    description: str = ""
    content: str = ""
    type: str = "note"


class FileMemoryManager:
    """Manages a .memory/ directory of markdown files.

    The agent saves long-lived knowledge as individual .md files.
    MEMORY.md serves as an always-loaded index so the agent knows
    what memories exist without reading every file.
    """

    def __init__(self, memory_dir: str):
        self._dir = Path(memory_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._index_path = self._dir / "MEMORY.md"

    # ── Core operations ──

    def save(self, topic: str, content: str, description: str = "",
             type: str = "note") -> str:
        """Save a memory file and rebuild the index.

        Args:
            topic: Short topic name (used as filename stem).
            content: Full memory content (markdown body).
            description: One-line summary for the index.
            type: Category tag (note, feedback, reference, project).

        Returns:
            The sanitized filename stem.
        """
        stem = self._sanitize(topic)
        filepath = self._dir / f"{stem}.md"

        # Write file with frontmatter
        desc_line = f"\ndescription: {description}" if description else ""
        header = f"---\nname: {topic}\ntype: {type}{desc_line}\n---\n\n"
        filepath.write_text(header + content + "\n", encoding="utf-8")

        self._rebuild_index()
        return stem

    def recall(self, query: str, top_k: int = 5) -> List[MemoryEntry]:
        """Search memories by keyword (case-insensitive substring match).

        Matches against name, description, and content.
        Returns up to top_k entries, most recently modified first.
        """
        q = query.lower()
        all_entries = self._load_all()
        scored = []
        for entry in all_entries:
            text = f"{entry.name} {entry.description} {entry.content}".lower()
            if q in text:
                scored.append(entry)
        # Sort by file mtime (newest first)
        scored.sort(key=lambda e: (self._dir / e.filename).stat().st_mtime,
                    reverse=True)
        return scored[:top_k]

    def list_all(self) -> List[MemoryEntry]:
        """Return all memory entries (index-level: name + description only)."""
        return self._load_all()

    def delete(self, topic: str) -> bool:
        """Delete a memory by topic name. Returns True if found and deleted."""
        stem = self._sanitize(topic)
        filepath = self._dir / f"{stem}.md"
        if filepath.exists():
            filepath.unlink()
            self._rebuild_index()
            return True
        return False

    def load_for_injection(self) -> str:
        """Load full MEMORY.md index for context injection.

        Returns the raw index text, or empty string if no memories exist.
        """
        if self._index_path.exists():
            return self._index_path.read_text(encoding="utf-8").strip()
        return ""

    # ── Internal ──

    def _load_all(self) -> List[MemoryEntry]:
        """Parse all .md files in the memory directory."""
        entries = []
        for filepath in sorted(self._dir.glob("*.md")):
            if filepath.name == "MEMORY.md":
                continue
            entries.append(self._parse_file(filepath))
        return entries

    @staticmethod
    def _parse_file(filepath: Path) -> MemoryEntry:
        """Parse a memory .md file with optional YAML frontmatter."""
        text = filepath.read_text(encoding="utf-8")
        name = filepath.stem
        description = ""
        mem_type = "note"
        content = text

        # Extract YAML frontmatter
        fm_match = re.match(r"^---\n(.*?)\n---\n(.*)", text, re.DOTALL)
        if fm_match:
            frontmatter = fm_match.group(1)
            content = fm_match.group(2).strip()
            for line in frontmatter.splitlines():
                if line.startswith("name:"):
                    name = line.split(":", 1)[1].strip()
                elif line.startswith("description:"):
                    description = line.split(":", 1)[1].strip()
                elif line.startswith("type:"):
                    mem_type = line.split(":", 1)[1].strip()

        return MemoryEntry(
            name=name,
            filename=filepath.name,
            description=description,
            content=content,
            type=mem_type,
        )

    def _rebuild_index(self):
        """Rebuild MEMORY.md from all memory files."""
        entries = self._load_all()
        if not entries:
            if self._index_path.exists():
                self._index_path.unlink()
            return

        lines = ["# Memory Index\n"]
        for entry in entries:
            desc = f" — {entry.description}" if entry.description else ""
            lines.append(f"- [{entry.name}]({entry.filename}){desc}")
        lines.append("")  # trailing newline

        self._index_path.write_text("\n".join(lines), encoding="utf-8")

    @staticmethod
    def _sanitize(topic: str) -> str:
        """Convert a topic to a safe filename stem.

        Replaces non-alphanumeric chars (except dots and hyphens) with
        underscores, collapses runs, strips leading/trailing underscores.
        """
        stem = re.sub(r"[^a-zA-Z0-9._-]", "_", topic.strip())
        stem = re.sub(r"_+", "_", stem).strip("_")
        if not stem:
            stem = "memory"
        # Limit length
        if len(stem) > 64:
            stem = stem[:64].rstrip("_")
        return stem
