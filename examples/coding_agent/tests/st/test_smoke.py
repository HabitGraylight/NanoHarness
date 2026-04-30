"""ST smoke tests for the coding agent example.

Run from examples/coding_agent/:
    PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest tests/st/ -v
"""

import os
import glob

import pytest

from nanoharness.components.context.simple_context import SimpleContextManager
from nanoharness.components.evaluator.trace_evaluator import TraceEvaluator
from nanoharness.components.hooks.simple_hooks import SimpleHookManager
from nanoharness.components.state.json_store import JsonStateStore
from nanoharness.core.engine import NanoEngine
from nanoharness.core.prompt import PromptManager
from nanoharness.core.schema import AgentMessage, LLMResponse, ToolCall


class MockLLMClient:
    """Stub LLM that returns a canned response with no tool calls."""

    def __init__(self, response: str = "Done."):
        self._response = response

    def chat(self, messages, tools=None) -> LLMResponse:
        return LLMResponse(content=self._response, tool_calls=None)


# ── Helpers ──


def _build_test_tools(tmp_path=None):
    from app.tools import build_tools
    scripts_dir = os.path.join(os.path.dirname(__file__), "..", "..", "configs", "scripts")
    workspace = str(tmp_path) if tmp_path else os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "..")
    )
    return build_tools(scripts_dir=scripts_dir, workspace_root=workspace)


def _build_test_perms():
    from app.permissions import build_permissions
    return build_permissions()


def _make_managed_context(tmp_path, llm=None, **kwargs):
    from app.context import ManagedContext
    scratch = str(tmp_path / "scratch")
    return ManagedContext(
        inner=SimpleContextManager(system_prompt="System."),
        scratch_dir=scratch,
        llm_client=llm,
        **kwargs,
    )


# ── Component tests ──


def test_tools_load():
    """All shell + Python tools register successfully."""
    workspace = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    scripts_dir = os.path.join(os.path.dirname(__file__), "..", "..", "configs", "scripts")
    tools = _build_test_tools()
    from app.dispatch import DispatchRegistry
    assert isinstance(tools, DispatchRegistry)
    schemas = tools.get_tool_schemas()
    assert len(schemas) >= 20
    names = [s["function"]["name"] for s in schemas]
    assert "file_read" in names
    assert "file_edit" in names
    assert "search_code" in names
    assert "list_files" in names


def test_prompts_load():
    """Coding-agent-specific prompts load from app/prompts.yaml."""
    prompts_path = os.path.join(os.path.dirname(__file__), "..", "..", "app", "prompts.yaml")
    pm = PromptManager.from_file(prompts_path)
    assert "segment.core_identity" in pm.keys()
    assert "software engineer" in pm.get("segment.core_identity").lower()


# ── Engine assembly tests ──


def test_engine_runs_with_mock_llm(tmp_path):
    """Engine runs to completion with a mock LLM."""
    llm = MockLLMClient(response="I have completed the task.")

    engine = NanoEngine(
        llm_client=llm,
        tools=_build_test_tools(tmp_path),
        context=SimpleContextManager(system_prompt="Test."),
        state=JsonStateStore(str(tmp_path / "state.json")),
        hooks=SimpleHookManager(),
        evaluator=TraceEvaluator(),
        permissions=_build_test_perms(),
        max_steps=5,
    )

    report = engine.run("Do something simple.")
    assert report["summary"]["total_steps"] == 1
    assert report["trajectory"][0]["status"] == "terminated"


def test_engine_with_tool_call(tmp_path):
    """Engine dispatches a tool call and returns observation."""
    # Create a test file so list_files finds something
    (tmp_path / "hello.py").write_text("pass")

    call_count = 0

    class ToolThenDone:
        def chat(self, messages, tools=None):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return LLMResponse(
                    content="Let me list files.",
                    tool_calls=[ToolCall(name="list_files", arguments={"pattern": "*.py", "path": "."})],
                )
            return LLMResponse(content="Done.", tool_calls=None)

    engine = NanoEngine(
        llm_client=ToolThenDone(),
        tools=_build_test_tools(tmp_path),
        context=SimpleContextManager(system_prompt="Test"),
        state=JsonStateStore(str(tmp_path / "state.json")),
        hooks=SimpleHookManager(),
        evaluator=TraceEvaluator(),
        permissions=_build_test_perms(),
        max_steps=5,
    )

    report = engine.run("List Python files.")
    assert report["summary"]["total_steps"] == 2
    assert ".py" in report["trajectory"][0]["observation"]


def test_builder_assembles(tmp_path, monkeypatch):
    """build_coding_engine() wires everything correctly."""
    from unittest.mock import patch
    from app.builder import build_coding_engine

    mock_llm = MockLLMClient(response="Task complete.")

    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")

    with patch("app.builder.OpenAIAdapter", return_value=mock_llm):
        engine = build_coding_engine()

    assert isinstance(engine, NanoEngine)
    report = engine.run("Test task")
    assert report["summary"]["total_steps"] >= 1


# ── Context management tests (three-layer) ──


def test_context_layer1_spill_large_obs(tmp_path):
    """Layer 1: large tool result gets spilled to disk, preview stays in context."""
    ctx = _make_managed_context(tmp_path, spill_threshold=200)
    long_content = "line\n" * 100  # 500 chars
    ctx.add_message(AgentMessage(role="tool", content=long_content))

    msg = ctx._messages[-1]
    assert msg.role == "tool"
    assert len(msg.content) < len(long_content)
    assert "saved to" in msg.content
    # Scratch file should exist
    spill_files = glob.glob(str(tmp_path / "scratch" / "spill_*.txt"))
    assert len(spill_files) == 1
    assert open(spill_files[0]).read() == long_content


def test_context_layer1_small_obs_stays(tmp_path):
    """Layer 1: small tool result stays in context unchanged."""
    ctx = _make_managed_context(tmp_path, spill_threshold=2000)
    ctx.add_message(AgentMessage(role="tool", content="Short result"))
    assert ctx._messages[-1].content == "Short result"


def test_context_layer1_non_tool_not_spilled(tmp_path):
    """Layer 1: non-tool messages are never spilled."""
    ctx = _make_managed_context(tmp_path, spill_threshold=10)
    long_text = "x" * 5000
    ctx.add_message(AgentMessage(role="assistant", content=long_text))
    assert ctx._messages[-1].content == long_text


def test_context_layer2_compress_old(tmp_path):
    """Layer 2: old tool observations get compressed to placeholders."""
    ctx = _make_managed_context(tmp_path, compress_chars=100)
    # Round 1 (old)
    ctx.add_message(AgentMessage(role="user", content="Task 1"))
    ctx.add_message(AgentMessage(role="assistant", content="Thinking..."))
    ctx.add_message(AgentMessage(role="tool", content="A" * 2000))  # long old obs
    ctx.add_message(AgentMessage(role="assistant", content="Done."))
    # Round 2 (current)
    ctx.add_message(AgentMessage(role="user", content="Task 2"))
    ctx.add_message(AgentMessage(role="tool", content="B" * 2000))  # recent obs

    ctx.compress_old()

    msgs = ctx._messages
    # Old tool obs compressed
    old_tool = msgs[3]
    assert old_tool.role == "tool"
    assert "compressed" in old_tool.content
    assert len(old_tool.content) < 200
    # Recent tool obs preserved
    recent_tool = msgs[6]
    assert recent_tool.role == "tool"
    assert len(recent_tool.content) == 2000
    # Assistant messages preserved
    assert msgs[2].content == "Thinking..."
    assert msgs[4].content == "Done."


def test_context_layer2_short_obs_kept(tmp_path):
    """Layer 2: short old observations pass through unchanged."""
    ctx = _make_managed_context(tmp_path, compress_chars=500)
    ctx.add_message(AgentMessage(role="user", content="Task"))
    ctx.add_message(AgentMessage(role="tool", content="Short output"))
    ctx.compress_old()
    tool_msgs = [m for m in ctx._messages if m.role == "tool"]
    assert tool_msgs[0].content == "Short output"


def test_context_layer3_summarize_when_long(tmp_path):
    """Layer 3: long context triggers LLM summarization."""
    class SummaryLLM:
        def chat(self, messages, tools=None):
            return LLMResponse(content="User asked to fix a bug. Assistant read the file.", tool_calls=None)

    ctx = _make_managed_context(tmp_path, llm=SummaryLLM(), token_limit=50)
    # Add many messages to exceed limit
    for i in range(20):
        ctx.add_message(AgentMessage(role="user", content=f"Task {i} " + "x" * 50))
        ctx.add_message(AgentMessage(role="assistant", content=f"Done {i}"))
        ctx.add_message(AgentMessage(role="tool", content=f"Result {i} " + "y" * 50))

    original_len = len(ctx._messages)
    ctx.summarize_if_needed()

    # Should be shorter
    assert len(ctx._messages) < original_len
    # System prompt should survive
    assert ctx._messages[0].role == "system"
    assert ctx._messages[0].content == "System."
    # Should have a summary message
    summaries = [m for m in ctx._messages if m.role == "system" and "Summary" in m.content]
    assert len(summaries) >= 1


def test_context_layer3_no_summarize_when_short(tmp_path):
    """Layer 3: short context is left alone."""
    ctx = _make_managed_context(tmp_path, token_limit=8000)
    ctx.add_message(AgentMessage(role="user", content="Hi"))
    ctx.add_message(AgentMessage(role="assistant", content="Hello"))
    original = list(ctx._messages)
    ctx.summarize_if_needed()
    assert ctx._messages == original


def test_context_layer3_fallback_without_llm(tmp_path):
    """Layer 3: without LLM, falls back to trimming."""
    ctx = _make_managed_context(tmp_path, llm=None, token_limit=20)
    for i in range(20):
        ctx.add_message(AgentMessage(role="user", content=f"Task {i} " * 20))
        ctx.add_message(AgentMessage(role="assistant", content=f"Done {i}"))
    ctx.summarize_if_needed()
    # System prompt survives, messages dropped
    assert ctx._messages[0].role == "system"
    assert len(ctx._messages) < 40
