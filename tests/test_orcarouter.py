import sys
from types import SimpleNamespace

import pytest

from nanoharness.components import OrcaRouterProvider
from nanoharness.components.llm import (
    ORCAROUTER_API_KEY_ENV,
    ORCAROUTER_BASE_URL,
)


class FakeCompletions:
    def __init__(self):
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        message = SimpleNamespace(content="done", tool_calls=[])
        choice = SimpleNamespace(message=message, finish_reason="stop")
        return SimpleNamespace(choices=[choice], usage=None, model=kwargs["model"])


def _client():
    completions = FakeCompletions()
    return SimpleNamespace(
        chat=SimpleNamespace(completions=completions),
        completions=completions,
    )


def test_orcarouter_uses_dedicated_environment_key_and_default_url(
    monkeypatch,
):
    constructed = []

    def openai_factory(**kwargs):
        constructed.append(kwargs)
        return _client()

    monkeypatch.setenv(ORCAROUTER_API_KEY_ENV, "sk-orca-test")
    monkeypatch.setitem(
        sys.modules,
        "openai",
        SimpleNamespace(OpenAI=openai_factory),
    )
    OrcaRouterProvider("openai/gpt-4o-mini")
    assert constructed == [{
        "api_key": "sk-orca-test",
        "base_url": ORCAROUTER_BASE_URL,
    }]


def test_orcarouter_requires_key_when_constructing_sdk_client(monkeypatch):
    monkeypatch.delenv(ORCAROUTER_API_KEY_ENV, raising=False)
    with pytest.raises(ValueError, match=ORCAROUTER_API_KEY_ENV):
        OrcaRouterProvider("orcarouter/auto")


def test_orcarouter_maps_fallback_chain_and_request_options():
    client = _client()
    provider = OrcaRouterProvider(
        "openai/gpt-4o-mini",
        client=client,
        fallback_models=[
            "anthropic/claude-haiku-4.5",
            "google/gemini-2.5-pro",
        ],
        extra_body={"custom": "value"},
        extra_headers={"X-Trace-Id": "trace-1"},
    )
    result = provider.chat(
        [{"role": "user", "content": "hello"}],
        tools=[{"type": "function"}],
    )
    request = client.completions.calls[0]
    assert result.content == "done"
    assert request["extra_body"] == {
        "custom": "value",
        "models": [
            "openai/gpt-4o-mini",
            "anthropic/claude-haiku-4.5",
            "google/gemini-2.5-pro",
        ],
        "route": "fallback",
    }
    assert request["extra_headers"] == {"X-Trace-Id": "trace-1"}
    assert request["tools"] == [{"type": "function"}]


@pytest.mark.parametrize(
    ("fallback_models", "message"),
    [
        ("anthropic/claude-haiku-4.5", "sequence"),
        ([""], "non-empty"),
        (["primary"], "duplicates"),
        (["a", "b", "c", "d", "e"], "at most 5"),
    ],
)
def test_orcarouter_rejects_invalid_fallback_chains(fallback_models, message):
    with pytest.raises((TypeError, ValueError), match=message):
        OrcaRouterProvider(
            "primary",
            client=_client(),
            fallback_models=fallback_models,
        )


@pytest.mark.parametrize("field", ["models", "route"])
def test_orcarouter_rejects_ambiguous_fallback_configuration(field):
    with pytest.raises(ValueError, match=field):
        OrcaRouterProvider(
            "primary",
            client=_client(),
            fallback_models=["secondary"],
            extra_body={field: "manual"},
        )


def test_orcarouter_requires_non_empty_base_url():
    with pytest.raises(ValueError, match="base_url is required"):
        OrcaRouterProvider("primary", client=_client(), base_url=" ")
