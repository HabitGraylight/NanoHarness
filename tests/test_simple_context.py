from nanoharness.components.context.simple_context import SimpleContextManager
from nanoharness.core.schema import AgentMessage


class TestSimpleContextManager:
    def test_empty(self):
        ctx = SimpleContextManager()
        assert ctx.get_full_context() == []

    def test_system_prompt(self):
        ctx = SimpleContextManager(system_prompt="You are helpful.")
        msgs = ctx.get_full_context()
        assert len(msgs) == 1
        assert msgs[0]["role"] == "system"
        assert msgs[0]["content"] == "You are helpful."

    def test_add_and_get(self):
        ctx = SimpleContextManager()
        ctx.add_message(AgentMessage(role="user", content="hi"))
        ctx.add_message(AgentMessage(role="assistant", content="hello"))
        msgs = ctx.get_full_context()
        assert len(msgs) == 2
        assert msgs[0]["role"] == "user"
        assert msgs[1]["role"] == "assistant"

    def test_reset(self):
        ctx = SimpleContextManager(system_prompt="sys")
        ctx.add_message(AgentMessage(role="user", content="hi"))
        assert len(ctx.get_full_context()) == 2
        ctx.reset()
        assert ctx.get_full_context() == []

    def test_preserves_explicit_tool_call_id(self):
        from nanoharness.core.schema import ToolCall

        ctx = SimpleContextManager()
        ctx.add_message(
            AgentMessage(
                role="assistant",
                content="",
                tool_calls=[
                    ToolCall(
                        name="read",
                        arguments={"path": "x.py"},
                        call_id="provider_call_1",
                    )
                ],
            )
        )
        ctx.add_message(
            AgentMessage(
                role="tool",
                content="content",
                tool_call_id="provider_call_1",
            )
        )

        messages = ctx.get_full_context()
        assert messages[0]["tool_calls"][0]["id"] == "provider_call_1"
        assert messages[1]["tool_call_id"] == "provider_call_1"
