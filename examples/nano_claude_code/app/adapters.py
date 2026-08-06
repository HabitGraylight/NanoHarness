"""LLM adapter — application-layer implementation.

Removed from the nanoharness kernel because it depends on external
packages (openai) and is not part of the ETCSLV governance components.
"""

import json
from typing import Any, Dict, List, Optional

from openai import OpenAI

from nanoharness.core.schema import LLMResponse, TokenUsage, ToolCall


class DetailedLLMResponse(LLMResponse):
    """LLMResponse with stop_reason — used by ResilientLLM for error recovery."""
    stop_reason: str = "end_turn"  # end_turn | tool_use | length | content_filter


class OpenAIAdapter:
    """OpenAI-compatible LLM adapter (satisfies LLMProtocol)."""

    def __init__(
        self,
        api_key: str,
        model: str = "deepseek-chat",
        base_url: Optional[str] = None,
    ):
        self._client = OpenAI(api_key=api_key, base_url=base_url)
        self._model = model

    def chat(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
    ) -> DetailedLLMResponse:
        kwargs = {"model": self._model, "messages": messages}
        if tools:
            kwargs["tools"] = tools
        resp = self._client.chat.completions.create(**kwargs)
        choice = resp.choices[0]
        tool_calls = None
        if choice.message.tool_calls:
            tool_calls = [
                ToolCall(
                    name=tc.function.name,
                    arguments=json.loads(tc.function.arguments),
                    call_id=tc.id if isinstance(getattr(tc, "id", None), str) else None,
                )
                for tc in choice.message.tool_calls
            ]
        usage = None
        usage_data = getattr(resp, "usage", None)
        if usage_data and isinstance(getattr(usage_data, "total_tokens", None), int):
            usage = TokenUsage(
                input_tokens=usage_data.prompt_tokens or 0,
                output_tokens=usage_data.completion_tokens or 0,
                total_tokens=usage_data.total_tokens or 0,
            )
        return DetailedLLMResponse(
            content=choice.message.content or "",
            tool_calls=tool_calls,
            model=resp.model if isinstance(getattr(resp, "model", None), str) else None,
            finish_reason=choice.finish_reason,
            usage=usage,
            stop_reason=choice.finish_reason or "end_turn",
        )
