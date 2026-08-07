from types import SimpleNamespace

import pytest

from app.provider import OpenAIChatProvider


class FakeCompletions:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


def _client(content="done", tool_calls=None, usage=None):
    message = SimpleNamespace(content=content, tool_calls=tool_calls or [])
    choice = SimpleNamespace(message=message, finish_reason="stop")
    response = SimpleNamespace(
        choices=[choice],
        usage=usage,
        model="provider-model",
    )
    completions = FakeCompletions(response)
    return SimpleNamespace(
        chat=SimpleNamespace(completions=completions),
        completions=completions,
    )


def test_provider_requires_model():
    with pytest.raises(ValueError, match="model is required"):
        OpenAIChatProvider(" ", client=_client())


def test_provider_maps_text_and_omits_empty_tools():
    client = _client("hello")
    result = OpenAIChatProvider("test", client=client).chat([{"role": "user"}])
    assert result.content == "hello"
    assert result.model == "provider-model"
    assert "tools" not in client.completions.calls[0]


def test_provider_maps_tool_calls_and_usage():
    call = SimpleNamespace(
        id="call_1",
        function=SimpleNamespace(name="workspace_read", arguments='{"path":"a.py"}'),
    )
    usage = SimpleNamespace(prompt_tokens=10, completion_tokens=3, total_tokens=13)
    client = _client("", [call], usage)
    result = OpenAIChatProvider("test", client=client).chat(
        [{"role": "user"}],
        tools=[{"type": "function"}],
    )
    assert result.tool_calls[0].call_id == "call_1"
    assert result.tool_calls[0].arguments == {"path": "a.py"}
    assert result.usage.total_tokens == 13
    assert client.completions.calls[0]["tools"] == [{"type": "function"}]


@pytest.mark.parametrize("arguments", ["not-json", "[]"])
def test_provider_rejects_invalid_tool_arguments(arguments):
    call = SimpleNamespace(
        id="call_bad",
        function=SimpleNamespace(name="bad", arguments=arguments),
    )
    with pytest.raises(ValueError, match="arguments"):
        OpenAIChatProvider("test", client=_client(tool_calls=[call])).chat([])
