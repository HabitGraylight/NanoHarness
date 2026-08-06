"""Network-free provider used to make Gallery scenarios reproducible."""

from copy import deepcopy
from typing import Any, Dict, List, Optional

from nanoharness.core.schema import LLMResponse

from app.schema import ScriptedResponse


class ScriptedLLM:
    """Replay provider-neutral responses and record only invocation metadata."""

    def __init__(self, responses: List[ScriptedResponse]):
        if not responses:
            raise ValueError("ScriptedLLM requires at least one response")
        self._responses = deepcopy(responses)
        self._index = 0
        self.calls: List[Dict[str, Any]] = []

    def chat(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
    ) -> LLMResponse:
        self.calls.append({
            "message_count": len(messages),
            "tool_schema_count": len(tools or []),
        })
        if self._index >= len(self._responses):
            return LLMResponse(content="Scenario response stream completed.")
        scripted = self._responses[self._index]
        self._index += 1
        return LLMResponse(
            content=scripted.content,
            tool_calls=[call.model_copy(deep=True) for call in scripted.tool_calls],
            model="gallery-scripted",
            finish_reason=("tool_calls" if scripted.tool_calls else "stop"),
        )

    @property
    def consumed(self) -> int:
        return self._index
