"""Shared fixtures for coding agent example tests."""

import sys
import os

# Ensure example directory is on sys.path
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, _ROOT)

from nanoharness.core.schema import LLMResponse


class MockLLMClient:
    """Stub LLM that returns a canned response with no tool calls."""

    def __init__(self, response: str = "Done."):
        self._response = response

    def chat(self, messages, tools=None) -> LLMResponse:
        return LLMResponse(content=self._response, tool_calls=None)
