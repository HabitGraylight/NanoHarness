"""LLM adapters owned by the NanoLoop application layer."""

import json
from typing import Any, Dict, List, Optional

from nanoharness.core.schema import LLMResponse, TokenUsage, ToolCall


class OpenAICompatibleAdapter:
    """DeepSeek/OpenAI-compatible implementation of ``LLMProtocol``."""

    def __init__(self, api_key: str, model: str, base_url: Optional[str] = None):
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise ImportError(
                "NanoLoop's default worker requires: pip install -e '.[openai]'"
            ) from exc
        self._client = OpenAI(api_key=api_key, base_url=base_url)
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
        message = response.choices[0].message
        tool_calls = None
        if message.tool_calls:
            tool_calls = [
                ToolCall(
                    name=call.function.name,
                    arguments=json.loads(call.function.arguments),
                    call_id=call.id if isinstance(getattr(call, "id", None), str) else None,
                )
                for call in message.tool_calls
            ]
        usage = None
        usage_data = getattr(response, "usage", None)
        if usage_data and isinstance(getattr(usage_data, "total_tokens", None), int):
            usage = TokenUsage(
                input_tokens=usage_data.prompt_tokens or 0,
                output_tokens=usage_data.completion_tokens or 0,
                total_tokens=usage_data.total_tokens or 0,
            )
        return LLMResponse(
            content=message.content or "",
            tool_calls=tool_calls,
            model=response.model if isinstance(getattr(response, "model", None), str) else None,
            finish_reason=response.choices[0].finish_reason,
            usage=usage,
        )
