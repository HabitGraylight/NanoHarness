"""Optional OpenAI-compatible implementation of the public LLM protocol."""

import json
from typing import Any, Optional

from nanoharness.core.schema import LLMResponse, TokenUsage, ToolCall


class OpenAIChatProvider:
    """Small OpenAI-compatible chat adapter with an injectable SDK client."""

    def __init__(
        self,
        model: str,
        *,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        client: Any = None,
    ):
        if not model.strip():
            raise ValueError("model is required")
        self.model = model
        if client is None:
            try:
                from openai import OpenAI
            except ImportError as error:
                raise RuntimeError(
                    "OpenAI provider requires the optional 'openai' package"
                ) from error
            kwargs = {}
            if api_key is not None:
                kwargs["api_key"] = api_key
            if base_url is not None:
                kwargs["base_url"] = base_url
            client = OpenAI(**kwargs)
        self.client = client

    def chat(self, messages, tools=None) -> LLMResponse:
        arguments = {"model": self.model, "messages": messages}
        if tools:
            arguments["tools"] = tools
        response = self.client.chat.completions.create(**arguments)
        choice = response.choices[0]
        message = choice.message
        calls = []
        for call in getattr(message, "tool_calls", None) or []:
            raw_arguments = call.function.arguments or "{}"
            try:
                parsed = json.loads(raw_arguments)
            except json.JSONDecodeError as error:
                raise ValueError(
                    f"provider returned invalid JSON arguments for {call.function.name!r}"
                ) from error
            if not isinstance(parsed, dict):
                raise ValueError("provider tool arguments must decode to an object")
            calls.append(ToolCall(
                call_id=getattr(call, "id", None),
                name=call.function.name,
                arguments=parsed,
            ))
        usage = getattr(response, "usage", None)
        token_usage = None
        if usage is not None:
            input_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
            output_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
            token_usage = TokenUsage(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=int(
                    getattr(usage, "total_tokens", input_tokens + output_tokens) or 0
                ),
            )
        return LLMResponse(
            content=getattr(message, "content", None) or "",
            tool_calls=calls or None,
            model=getattr(response, "model", None) or self.model,
            finish_reason=getattr(choice, "finish_reason", None),
            usage=token_usage,
        )
