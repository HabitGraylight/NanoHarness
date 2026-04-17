from typing import Any, Dict, List, Optional

import pytest

from nanoharness.core.schema import LLMResponse, ToolCall


class MockLLMClient:
    """Minimal mock satisfying LLMProtocol for testing."""

    def __init__(self, responses: Optional[List[LLMResponse]] = None):
        self.responses = responses or []
        self._call_idx = 0

    def chat(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
    ) -> LLMResponse:
        if self._call_idx >= len(self.responses):
            # Default: terminate immediately
            return LLMResponse(content="done")
        resp = self.responses[self._call_idx]
        self._call_idx += 1
        return resp


@pytest.fixture
def mock_llm():
    return MockLLMClient
