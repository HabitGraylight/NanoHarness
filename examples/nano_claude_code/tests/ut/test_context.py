"""Tests for ManagedContext -- three-layer context: spill -> compress -> summarize."""
import os

import pytest

from nanoharness.core.schema import AgentMessage
from nanoharness.components.context.simple_context import SimpleContextManager
from app.context import ManagedContext, _make_placeholder, _extract_key_facts, verify_goal


# -- Helpers --


class FakeLLMForContext:
    """Fake LLM that returns a canned summary response."""
    def __init__(self, response="Summary of conversation."):
        self._response = response

    def chat(self, messages, tools=None):
        from nanoharness.core.schema import LLMResponse
        return LLMResponse(content=self._response, tool_calls=None)


# -- Layer 1: Spill --


class TestSpillLargeObs:
    def test_spill_large_obs(self, tmp_path):
        """add_message with large tool result spills to disk, keeps preview."""
        scratch = os.path.join(str(tmp_path), "scratch")
        ctx = ManagedContext(
            inner=SimpleContextManager(system_prompt="test"),
            scratch_dir=scratch,
            spill_threshold=100,
        )
        large_content = "\n".join(f"line {i}: " + "x" * 100 for i in range(100))
        msg = AgentMessage(role="tool", content=large_content)
        ctx.add_message(msg)

        # Should have spilled to disk
        spill_files = [f for f in os.listdir(scratch) if f.startswith("spill_")]
        assert len(spill_files) == 1

        # Message in context should be a preview (shorter than original)
        messages = ctx._messages
        tool_msgs = [m for m in messages if m.role == "tool"]
        assert len(tool_msgs) == 1
        assert len(tool_msgs[0].content) < len(large_content)
        assert "file_read" in tool_msgs[0].content  # spill reference

    def test_spill_small_obs_stays(self, tmp_path):
        """Small tool result stays in context."""
        scratch = os.path.join(str(tmp_path), "scratch")
        ctx = ManagedContext(
            inner=SimpleContextManager(system_prompt="test"),
            scratch_dir=scratch,
            spill_threshold=2000,
        )
        small_content = "just a small result"
        msg = AgentMessage(role="tool", content=small_content)
        ctx.add_message(msg)

        # No spill files created
        spill_files = [f for f in os.listdir(scratch) if f.startswith("spill_")]
        assert len(spill_files) == 0

        # Message preserved as-is
        messages = ctx._messages
        tool_msgs = [m for m in messages if m.role == "tool"]
        assert len(tool_msgs) == 1
        assert tool_msgs[0].content == small_content

    def test_spill_non_tool_not_spilled(self, tmp_path):
        """Non-tool messages never spilled."""
        scratch = os.path.join(str(tmp_path), "scratch")
        ctx = ManagedContext(
            inner=SimpleContextManager(system_prompt="test"),
            scratch_dir=scratch,
            spill_threshold=100,
        )
        large_content = "y" * 500
        msg = AgentMessage(role="user", content=large_content)
        ctx.add_message(msg)

        # No spill files created
        spill_files = [f for f in os.listdir(scratch) if f.startswith("spill_")]
        assert len(spill_files) == 0


# -- Layer 2: Compress --


class TestCompressOld:
    def test_compress_old(self, tmp_path):
        """Old tool observations compressed to placeholders."""
        scratch = os.path.join(str(tmp_path), "scratch")
        ctx = ManagedContext(
            inner=SimpleContextManager(system_prompt="test"),
            scratch_dir=scratch,
            compress_chars=50,
        )
        # Add old messages (before user message)
        ctx.add_message(AgentMessage(role="tool", content="line\n" * 100))
        # Add user message to mark "current round"
        ctx.add_message(AgentMessage(role="user", content="new request"))

        ctx.compress_old()

        # Tool message should be compressed
        tool_msgs = [m for m in ctx._messages if m.role == "tool"]
        assert len(tool_msgs) == 1
        assert "[compressed]" in tool_msgs[0].content
        assert len(tool_msgs[0].content) < 500

    def test_compress_short_obs_kept(self, tmp_path):
        """Short old observations pass through."""
        scratch = os.path.join(str(tmp_path), "scratch")
        ctx = ManagedContext(
            inner=SimpleContextManager(system_prompt="test"),
            scratch_dir=scratch,
            compress_chars=500,
        )
        short_content = "short observation"
        ctx.add_message(AgentMessage(role="tool", content=short_content))
        ctx.add_message(AgentMessage(role="user", content="new request"))

        ctx.compress_old()

        tool_msgs = [m for m in ctx._messages if m.role == "tool"]
        assert len(tool_msgs) == 1
        assert tool_msgs[0].content == short_content


# -- Layer 3: Summarize --


class TestSummarizeWhenLong:
    def test_summarize_when_long(self, tmp_path):
        """Long context triggers LLM summarization."""
        scratch = os.path.join(str(tmp_path), "scratch")
        fake_llm = FakeLLMForContext(response="Summary: user asked about files.")

        ctx = ManagedContext(
            inner=SimpleContextManager(system_prompt="test system prompt"),
            scratch_dir=scratch,
            llm_client=fake_llm,
            token_limit=10,  # very low to trigger summarization
        )
        # Add enough messages to exceed token limit
        for i in range(20):
            ctx.add_message(AgentMessage(role="user", content=f"User message {i} long filler text"))
            ctx.add_message(AgentMessage(role="assistant", content=f"Assistant response {i} more filler"))

        ctx.summarize_if_needed()

        # Should have system prompt + summary + recent messages
        summary_msgs = [m for m in ctx._messages if m.role == "system" and "Conversation Summary" in m.content]
        assert len(summary_msgs) == 1
        assert "files" in summary_msgs[0].content


class TestNoSummarizeWhenShort:
    def test_no_summarize_when_short(self, tmp_path):
        """Short context left alone."""
        scratch = os.path.join(str(tmp_path), "scratch")
        fake_llm = FakeLLMForContext()

        ctx = ManagedContext(
            inner=SimpleContextManager(system_prompt="test"),
            scratch_dir=scratch,
            llm_client=fake_llm,
            token_limit=100000,  # very high, no summarization
        )
        ctx.add_message(AgentMessage(role="user", content="hello"))
        ctx.add_message(AgentMessage(role="assistant", content="hi"))

        ctx.summarize_if_needed()

        # No summary message added
        summary_msgs = [m for m in ctx._messages if m.role == "system" and "Conversation Summary" in m.content]
        assert len(summary_msgs) == 0


class TestFallbackWithoutLLM:
    def test_fallback_without_llm(self, tmp_path):
        """Without LLM, falls back to trimming."""
        scratch = os.path.join(str(tmp_path), "scratch")

        ctx = ManagedContext(
            inner=SimpleContextManager(system_prompt="test"),
            scratch_dir=scratch,
            llm_client=None,
            token_limit=10,  # very low to trigger trimming
        )
        for i in range(20):
            ctx.add_message(AgentMessage(role="user", content=f"User message {i} with lots of text to exceed limit"))
            ctx.add_message(AgentMessage(role="assistant", content=f"Response {i}"))

        ctx.summarize_if_needed()

        # Should have trimmed some messages (fewer than original)
        non_system = [m for m in ctx._messages if m.role != "system"]
        assert len(non_system) < 40  # was 40 messages, some trimmed


# -- Placeholder helper --


class TestMakePlaceholder:
    def test_make_placeholder(self):
        """_make_placeholder produces truncated output with [compressed] marker."""
        long_content = "line one\nline two\nline three\n" * 50
        result = _make_placeholder(long_content, 100)
        assert "[compressed]" in result
        assert len(result) <= 200  # well under full content
        assert "line one" in result


# -- Extract key facts --


class TestExtractKeyFacts:
    def test_extract_key_facts(self):
        """_extract_key_facts extracts user/assistant messages."""
        messages = [
            AgentMessage(role="user", content="Fix the bug in parser"),
            AgentMessage(role="assistant", content="I found the issue"),
            AgentMessage(role="tool", content="file contents here"),
        ]
        result = _extract_key_facts(messages)
        assert "Fix the bug" in result
        assert "found the issue" in result


# -- Goal verification --


class TestVerifyGoalAchieved:
    def test_verify_goal_achieved(self):
        """verify_goal returns True for ACHIEVED response."""
        fake_llm = FakeLLMForContext(response="ACHIEVED: The task was completed successfully.")
        achieved, explanation = verify_goal(
            fake_llm,
            "Fix the bug",
            {"trajectory": [{"status": "done", "action": {"name": "file_edit"}, "observation": "ok", "thought": "done"}]},
        )
        assert achieved is True
        assert "ACHIEVED" in explanation

    def test_verify_goal_not_achieved(self):
        """verify_goal returns False for NOT_ACHIEVED response."""
        fake_llm = FakeLLMForContext(response="NOT_ACHIEVED: The file was not found.")
        achieved, explanation = verify_goal(
            fake_llm,
            "Delete the temp file",
            {"trajectory": [{"status": "error", "action": {"name": "file_read"}, "observation": "not found", "thought": "error"}]},
        )
        assert achieved is False
        assert "NOT_ACHIEVED" in explanation
