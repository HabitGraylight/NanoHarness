import json

from app.context import LoopContextManager
from nanoharness.core.schema import AgentMessage, ToolCall


def test_context_formats_tool_calls_and_matching_tool_ids():
    context = LoopContextManager("system")
    context.add_message(AgentMessage(role="user", content="read it"))
    context.add_message(
        AgentMessage(
            role="assistant",
            content="",
            tool_calls=[ToolCall(name="file_read", arguments={"path": "a.py"})],
        )
    )
    context.add_message(AgentMessage(role="tool", content="content"))

    messages = context.get_full_context()
    call = messages[2]["tool_calls"][0]
    assert call["type"] == "function"
    assert json.loads(call["function"]["arguments"]) == {"path": "a.py"}
    assert messages[3]["tool_call_id"] == call["id"]


def test_context_reset_restores_system_prompt():
    context = LoopContextManager("system")
    context.add_message(AgentMessage(role="user", content="hello"))
    context.reset()
    assert context.get_full_context() == [{"role": "system", "content": "system"}]
