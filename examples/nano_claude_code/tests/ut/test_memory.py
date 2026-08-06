"""Tests for FileMemoryManager -- file-based memory with .memory/ directory."""
import os
import pytest
from app.memory import FileMemoryManager


# -- Save --


class TestSaveCreatesFile:
    def test_save_creates_file(self, tmp_path):
        """save() creates a .md file in the memory directory."""
        mem = FileMemoryManager(str(tmp_path / "mem"))
        stem = mem.save("preferences", "User prefers tabs over spaces.")
        filepath = tmp_path / "mem" / f"{stem}.md"
        assert filepath.exists()
        assert "tabs over spaces" in filepath.read_text()


class TestSaveRebuildsIndex:
    def test_save_rebuilds_index(self, tmp_path):
        """save() creates/updates MEMORY.md index."""
        mem = FileMemoryManager(str(tmp_path / "mem"))
        mem.save("preferences", "User prefers tabs.")
        index_path = tmp_path / "mem" / "MEMORY.md"
        assert index_path.exists()
        content = index_path.read_text()
        assert "preferences" in content


class TestSaveReturnsStem:
    def test_save_returns_stem(self, tmp_path):
        """save() returns the sanitized filename stem."""
        mem = FileMemoryManager(str(tmp_path / "mem"))
        stem = mem.save("my topic", "Some content")
        assert stem == "my_topic"


# -- Recall --


class TestRecallFindsByKeyword:
    def test_recall_finds_by_keyword(self, tmp_path):
        """recall() finds entries by keyword (case-insensitive)."""
        mem = FileMemoryManager(str(tmp_path / "mem"))
        mem.save("python style", "Use 4 spaces for indentation.", description="Python style guide")
        mem.save("git workflow", "Use feature branches.", description="Git process")
        results = mem.recall("python")
        assert len(results) == 1
        assert results[0].name == "python style"


class TestRecallEmptyNoMatch:
    def test_recall_empty_no_match(self, tmp_path):
        """recall() returns empty when no match."""
        mem = FileMemoryManager(str(tmp_path / "mem"))
        mem.save("python style", "Use 4 spaces.")
        results = mem.recall("javascript")
        assert results == []


class TestRecallTopK:
    def test_recall_top_k(self, tmp_path):
        """recall() respects top_k limit."""
        mem = FileMemoryManager(str(tmp_path / "mem"))
        mem.save("python style", "Python style guide")
        mem.save("python testing", "Python testing guide")
        mem.save("python linting", "Python linting guide")
        results = mem.recall("python", top_k=2)
        assert len(results) == 2


# -- List all --


class TestListAll:
    def test_list_all(self, tmp_path):
        """list_all() returns all entries."""
        mem = FileMemoryManager(str(tmp_path / "mem"))
        mem.save("topic_a", "Content A")
        mem.save("topic_b", "Content B")
        entries = mem.list_all()
        assert len(entries) == 2


class TestListAllEmpty:
    def test_list_all_empty(self, tmp_path):
        """list_all() returns empty for new directory."""
        mem = FileMemoryManager(str(tmp_path / "mem"))
        entries = mem.list_all()
        assert entries == []


# -- Delete --


class TestDeleteRemovesFile:
    def test_delete_removes_file(self, tmp_path):
        """delete() removes the .md file."""
        mem = FileMemoryManager(str(tmp_path / "mem"))
        mem.save("to_delete", "Will be deleted")
        assert mem.delete("to_delete") is True
        entries = mem.list_all()
        assert len(entries) == 0


class TestDeleteReturnsFalseIfMissing:
    def test_delete_returns_false_if_missing(self, tmp_path):
        """delete() returns False for nonexistent."""
        mem = FileMemoryManager(str(tmp_path / "mem"))
        assert mem.delete("nonexistent") is False


class TestDeleteRebuildsIndex:
    def test_delete_rebuilds_index(self, tmp_path):
        """delete() rebuilds MEMORY.md."""
        mem = FileMemoryManager(str(tmp_path / "mem"))
        mem.save("keep_this", "Should remain")
        mem.save("delete_this", "Should go away")
        mem.delete("delete_this")
        index_path = tmp_path / "mem" / "MEMORY.md"
        content = index_path.read_text()
        assert "keep_this" in content
        assert "delete_this" not in content


# -- Sanitize --


class TestSanitizeConvertsSpaces:
    def test_sanitize_converts_spaces(self, tmp_path):
        """_sanitize converts spaces to underscores."""
        mem = FileMemoryManager(str(tmp_path / "mem"))
        assert mem._sanitize("hello world") == "hello_world"


class TestSanitizeStripsSpecial:
    def test_sanitize_strips_special(self, tmp_path):
        """_sanitize strips special characters."""
        mem = FileMemoryManager(str(tmp_path / "mem"))
        result = mem._sanitize("hello!@#$world")
        assert "!" not in result
        assert "@" not in result


class TestSanitizeLimitsLength:
    def test_sanitize_limits_length(self, tmp_path):
        """_sanitize limits to 64 chars."""
        mem = FileMemoryManager(str(tmp_path / "mem"))
        long_topic = "a" * 100
        result = mem._sanitize(long_topic)
        assert len(result) <= 64


class TestSanitizeEmptyBecomesMemory:
    def test_sanitize_empty_becomes_memory(self, tmp_path):
        """_sanitize returns 'memory' for empty input."""
        mem = FileMemoryManager(str(tmp_path / "mem"))
        assert mem._sanitize("") == "memory"
        assert mem._sanitize("   ") == "memory"


# -- Load for injection --


class TestLoadForInjection:
    def test_load_for_injection(self, tmp_path):
        """load_for_injection() returns MEMORY.md content."""
        mem = FileMemoryManager(str(tmp_path / "mem"))
        mem.save("test_topic", "Some content", description="A test")
        content = mem.load_for_injection()
        assert "test_topic" in content

    def test_load_for_injection_empty(self, tmp_path):
        """Returns empty string when no memories."""
        mem = FileMemoryManager(str(tmp_path / "mem"))
        content = mem.load_for_injection()
        assert content == ""


# -- Parse file --


class TestParseFileWithFrontmatter:
    def test_parse_file_with_frontmatter(self, tmp_path):
        """Parses name, type, description from frontmatter."""
        mem = FileMemoryManager(str(tmp_path / "mem"))
        filepath = tmp_path / "mem" / "test.md"
        filepath.parent.mkdir(parents=True, exist_ok=True)
        filepath.write_text(
            "---\nname: my-topic\ndescription: A description\ntype: feedback\n---\n\nBody content here.\n"
        )
        entry = mem._parse_file(filepath)
        assert entry.name == "my-topic"
        assert entry.description == "A description"
        assert entry.type == "feedback"
        assert entry.content == "Body content here."


class TestParseFileNoFrontmatter:
    def test_parse_file_no_frontmatter(self, tmp_path):
        """Falls back to filename as name."""
        mem = FileMemoryManager(str(tmp_path / "mem"))
        filepath = tmp_path / "mem" / "simple-note.md"
        filepath.parent.mkdir(parents=True, exist_ok=True)
        filepath.write_text("Just some body text.\n")
        entry = mem._parse_file(filepath)
        assert entry.name == "simple-note"
        assert entry.description == ""
        assert "Just some body text" in entry.content
