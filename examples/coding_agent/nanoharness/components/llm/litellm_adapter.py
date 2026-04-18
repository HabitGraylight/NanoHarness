import json
from typing import Any, Dict, List, Optional

from nanoharness.core.schema import LLMResponse, ToolCall


class LiteLLMAdapter:
    """Universal adapter using litellm — routes to 100+ providers.

    Model prefix determines the provider:
        "deepseek/deepseek-chat"
        "claude-sonnet-4-5-20250514"
        "ollama/llama3"
        ...
    """

    def __init__(
        self,
        model: str = "deepseek/deepseek-chat",
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
    ):
        import litellm

        self._litellm = litellm
        self._model = model
        self._api_key = api_key
        self._base_url = base_url

    def chat(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
    ) -> LLMResponse:
        kwargs: Dict[str, Any] = {
            "model": self._model,
            "messages": messages,
        }
        if self._api_key:
            kwargs["api_key"] = self._api_key
        if self._base_url:
            kwargs["api_base"] = self._base_url
        if tools:
            kwargs["tools"] = tools

        response = self._litellm.completion(**kwargs)
        choice = response.choices[0].message

        tool_calls = None
        if choice.tool_calls:
            tool_calls = [
                ToolCall(
                    name=tc.function.name,
                    arguments=json.loads(tc.function.arguments),
                )
                for tc in choice.tool_calls
            ]

        return LLMResponse(content=choice.content or "", tool_calls=tool_calls)
