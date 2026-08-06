"""Tests for LLM adapter interface -- DetailedLLMResponse and OpenAIAdapter protocol."""
import json
from unittest.mock import MagicMock

import pytest

from nanoharness.core.schema import ToolCall
from app.adapters import DetailedLLMResponse, OpenAIAdapter


# -- DetailedLLMResponse --


class TestDetailedResponseDefaults:
    def test_detailed_response_defaults(self):
        """DetailedLLMResponse has stop_reason='end_turn'."""
        resp = DetailedLLMResponse(content="hello")
        assert resp.stop_reason == "end_turn"


class TestDetailedResponseCustomStop:
    def test_detailed_response_custom_stop(self):
        """Can set custom stop_reason."""
        resp = DetailedLLMResponse(content="hello", stop_reason="tool_use")
        assert resp.stop_reason == "tool_use"


class TestDetailedResponseInheritsFields:
    def test_detailed_response_inherits_fields(self):
        """Has content, tool_calls from parent."""
        tc = ToolCall(name="test", arguments={"key": "val"})
        resp = DetailedLLMResponse(content="result", tool_calls=[tc])
        assert resp.content == "result"
        assert resp.tool_calls is not None
        assert len(resp.tool_calls) == 1
        assert resp.tool_calls[0].name == "test"


# -- OpenAIAdapter tool call parsing --


class TestAdapterToolCallParsing:
    def test_adapter_tool_call_parsing(self):
        """Mock OpenAI client, verify ToolCall extraction from chat response."""
        # Build a mock OpenAI client
        mock_client = MagicMock()

        # Mock the function tool call object
        mock_function = MagicMock()
        mock_function.name = "file_read"
        mock_function.arguments = json.dumps({"path": "/tmp/test.py"})

        mock_tool_call = MagicMock()
        mock_tool_call.function = mock_function

        # Mock the message
        mock_message = MagicMock()
        mock_message.content = "Reading the file."
        mock_message.tool_calls = [mock_tool_call]

        # Mock the choice
        mock_choice = MagicMock()
        mock_choice.message = mock_message
        mock_choice.finish_reason = "tool_use"

        # Mock the response
        mock_response = MagicMock()
        mock_response.choices = [mock_choice]

        mock_client.chat.completions.create.return_value = mock_response

        # Create adapter with mock client
        adapter = OpenAIAdapter(api_key="fake-key", model="test-model")
        adapter._client = mock_client

        result = adapter.chat(
            messages=[{"role": "user", "content": "Read the file"}],
            tools=[{"type": "function", "function": {"name": "file_read"}}],
        )

        assert isinstance(result, DetailedLLMResponse)
        assert result.content == "Reading the file."
        assert result.tool_calls is not None
        assert len(result.tool_calls) == 1
        assert result.tool_calls[0].name == "file_read"
        assert result.tool_calls[0].arguments == {"path": "/tmp/test.py"}
        assert result.stop_reason == "tool_use"


class TestAdapterNoToolCalls:
    def test_adapter_no_tool_calls(self):
        """When message has no tool_calls, returns None."""
        mock_client = MagicMock()

        mock_message = MagicMock()
        mock_message.content = "Hello!"
        mock_message.tool_calls = None

        mock_choice = MagicMock()
        mock_choice.message = mock_message
        mock_choice.finish_reason = "end_turn"

        mock_response = MagicMock()
        mock_response.choices = [mock_choice]

        mock_client.chat.completions.create.return_value = mock_response

        adapter = OpenAIAdapter(api_key="fake-key")
        adapter._client = mock_client

        result = adapter.chat(messages=[{"role": "user", "content": "hi"}])

        assert result.tool_calls is None
        assert result.content == "Hello!"
        assert result.stop_reason == "end_turn"


class TestAdapterEmptyContent:
    def test_adapter_empty_content(self):
        """Handles None content from API (returns empty string)."""
        mock_client = MagicMock()

        mock_message = MagicMock()
        mock_message.content = None
        mock_message.tool_calls = None

        mock_choice = MagicMock()
        mock_choice.message = mock_message
        mock_choice.finish_reason = "end_turn"

        mock_response = MagicMock()
        mock_response.choices = [mock_choice]

        mock_client.chat.completions.create.return_value = mock_response

        adapter = OpenAIAdapter(api_key="fake-key")
        adapter._client = mock_client

        result = adapter.chat(messages=[{"role": "user", "content": "hi"}])

        assert result.content == ""
