import json
from typing import Any, Dict, List, Optional

from nanoharness.core.schema import LLMResponse, ToolCall


class OpenAIAdapter:
    """Adapter for OpenAI-compatible APIs (OpenAI, DeepSeek, Moonshot, etc.).

    Set base_url to point at any OpenAI-compatible endpoint.
    """

    def __init__(
        self,
        api_key: str,
        model: str = "deepseek-chat",
        base_url: Optional[str] = None,
    ):
        from openai import OpenAI

        kwargs: Dict[str, Any] = {"api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url
        self._client = OpenAI(**kwargs)
        self._model = model

    def chat(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
    ) -> LLMResponse:
        kwargs: Dict[str, Any] = {
            "model": self._model,
            "messages": messages,
        }
        if tools:
            kwargs["tools"] = tools

        response = self._client.chat.completions.create(**kwargs)
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
