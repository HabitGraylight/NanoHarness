"""Portable file-backed memory store used by the Memory extension."""

import re
from dataclasses import dataclass
from pathlib import Path
from typing import List


@dataclass
class MemoryEntry:
    """A single memory entry read from a Markdown file."""

    name: str
    filename: str
    description: str = ""
    content: str = ""
    type: str = "note"


class FileMemoryManager:
    """Manage individual Markdown memories plus an always-loaded index."""

    def __init__(self, memory_dir: str):
        self._dir = Path(memory_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._index_path = self._dir / "MEMORY.md"

    @property
    def directory(self) -> Path:
        return self._dir

    def save(
        self,
        topic: str,
        content: str,
        description: str = "",
        type: str = "note",
    ) -> str:
        stem = self._sanitize(topic)
        filepath = self._dir / f"{stem}.md"
        desc_line = f"\ndescription: {description}" if description else ""
        header = f"---\nname: {topic}\ntype: {type}{desc_line}\n---\n\n"
        filepath.write_text(header + content + "\n", encoding="utf-8")
        self._rebuild_index()
        return stem

    def recall(self, query: str, top_k: int = 5) -> List[MemoryEntry]:
        q = query.lower()
        scored = []
        for entry in self._load_all():
            text = f"{entry.name} {entry.description} {entry.content}".lower()
            if q in text:
                scored.append(entry)
        scored.sort(
            key=lambda entry: (self._dir / entry.filename).stat().st_mtime,
            reverse=True,
        )
        return scored[:top_k]

    def list_all(self) -> List[MemoryEntry]:
        return self._load_all()

    def delete(self, topic: str) -> bool:
        filepath = self._dir / f"{self._sanitize(topic)}.md"
        if not filepath.exists():
            return False
        filepath.unlink()
        self._rebuild_index()
        return True

    def load_for_injection(self) -> str:
        if self._index_path.exists():
            return self._index_path.read_text(encoding="utf-8").strip()
        return ""

    def _load_all(self) -> List[MemoryEntry]:
        entries = []
        for filepath in sorted(self._dir.glob("*.md")):
            if filepath.name != "MEMORY.md":
                entries.append(self._parse_file(filepath))
        return entries

    @staticmethod
    def _parse_file(filepath: Path) -> MemoryEntry:
        text = filepath.read_text(encoding="utf-8")
        name = filepath.stem
        description = ""
        memory_type = "note"
        content = text

        frontmatter_match = re.match(r"^---\n(.*?)\n---\n(.*)", text, re.DOTALL)
        if frontmatter_match:
            frontmatter = frontmatter_match.group(1)
            content = frontmatter_match.group(2).strip()
            for line in frontmatter.splitlines():
                if line.startswith("name:"):
                    name = line.split(":", 1)[1].strip()
                elif line.startswith("description:"):
                    description = line.split(":", 1)[1].strip()
                elif line.startswith("type:"):
                    memory_type = line.split(":", 1)[1].strip()

        return MemoryEntry(
            name=name,
            filename=filepath.name,
            description=description,
            content=content,
            type=memory_type,
        )

    def _rebuild_index(self) -> None:
        entries = self._load_all()
        if not entries:
            if self._index_path.exists():
                self._index_path.unlink()
            return

        lines = ["# Memory Index\n"]
        for entry in entries:
            description = f" — {entry.description}" if entry.description else ""
            lines.append(f"- [{entry.name}]({entry.filename}){description}")
        lines.append("")
        self._index_path.write_text("\n".join(lines), encoding="utf-8")

    @staticmethod
    def _sanitize(topic: str) -> str:
        stem = re.sub(r"[^a-zA-Z0-9._-]", "_", topic.strip())
        stem = re.sub(r"_+", "_", stem).strip("_")
        if not stem:
            stem = "memory"
        if len(stem) > 64:
            stem = stem[:64].rstrip("_")
        return stem
