from typing import Any, Dict, List, Optional

from nanoharness.core.schema import LLMResponse, ToolCall


class AnthropicAdapter:
    """Adapter for the Anthropic Messages API (Claude family)."""

    def __init__(
        self,
        api_key: str,
        model: str = "claude-sonnet-4-5-20250514",
        max_tokens: int = 4096,
    ):
        from anthropic import Anthropic

        self._client = Anthropic(api_key=api_key)
        self._model = model
        self._max_tokens = max_tokens

    def chat(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
    ) -> LLMResponse:
        # Anthropic separates system messages from the message list
        system = None
        formatted: List[Dict[str, Any]] = []
        for msg in messages:
            if msg.get("role") == "system":
                system = msg["content"]
            else:
                formatted.append(msg)

        kwargs: Dict[str, Any] = {
            "model": self._model,
            "messages": formatted,
            "max_tokens": self._max_tokens,
        }
        if system:
            kwargs["system"] = system

        if tools:
            # Convert OpenAI-style tool schemas to Anthropic format
            kwargs["tools"] = [
                {
                    "name": t["function"]["name"],
                    "description": t["function"]["description"],
                    "input_schema": t["function"]["parameters"],
                }
                for t in tools
            ]

        response = self._client.messages.create(**kwargs)

        tool_calls = None
        content = ""
        for block in response.content:
            if block.type == "text":
                content += block.text
            elif block.type == "tool_use":
                if tool_calls is None:
                    tool_calls = []
                tool_calls.append(
                    ToolCall(name=block.name, arguments=block.input)
                )

        return LLMResponse(content=content, tool_calls=tool_calls)
