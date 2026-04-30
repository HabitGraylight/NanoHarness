"""Tests for ResilientLLM error recovery wrapper."""

from unittest.mock import MagicMock, patch

import pytest
from openai import APIConnectionError, APITimeoutError, BadRequestError, RateLimitError

from nanoharness.core.schema import LLMResponse, ToolCall

from app.adapters import DetailedLLMResponse
from app.resilient_llm import ResilientLLM


# ── Helpers ──


def _mock_response(status_code=200):
    """Create a mock httpx.Response for OpenAI exception constructors."""
    r = MagicMock()
    r.status_code = status_code
    return r


def _make_response(content="ok", tool_calls=None, stop_reason="end_turn"):
    return DetailedLLMResponse(
        content=content,
        tool_calls=tool_calls,
        stop_reason=stop_reason,
    )


def _messages():
    return [{"role": "user", "content": "hello"}]


# ── max_tokens continuation ──


class TestMaxTokensContinuation:
    def test_single_continuation(self):
        """Two-part response: first truncated, second completes."""
        inner = MagicMock()
        inner.chat.side_effect = [
            _make_response(content="Part 1...", stop_reason="length"),
            _make_response(content=" Part 2.", stop_reason="end_turn"),
        ]

        llm = ResilientLLM(inner, max_continuations=3)
        result = llm.chat(_messages())

        assert result.content == "Part 1... Part 2."
        assert inner.chat.call_count == 2
        # Second call should have continuation prompt appended
        second_call_msgs = inner.chat.call_args_list[1][0][0]
        assert second_call_msgs[-1]["role"] == "user"
        assert "truncated" in second_call_msgs[-1]["content"].lower()

    def test_multi_continuation(self):
        """Three-part response: truncated twice, then completes."""
        inner = MagicMock()
        inner.chat.side_effect = [
            _make_response(content="A", stop_reason="length"),
            _make_response(content="B", stop_reason="length"),
            _make_response(content="C", stop_reason="end_turn"),
        ]

        llm = ResilientLLM(inner, max_continuations=3)
        result = llm.chat(_messages())

        assert result.content == "ABC"
        assert inner.chat.call_count == 3

    def test_continuation_limit_exceeded(self):
        """If continuations exceed limit, return what we have."""
        inner = MagicMock()
        # Always returns length — will hit max_continuations=2
        inner.chat.side_effect = [
            _make_response(content="A", stop_reason="length"),
            _make_response(content="B", stop_reason="length"),
            _make_response(content="C", stop_reason="length"),
        ]

        llm = ResilientLLM(inner, max_continuations=2)
        result = llm.chat(_messages())

        # Should accumulate A + B + C even though all hit length
        assert result.content == "ABC"
        assert inner.chat.call_count == 3

    def test_continuation_with_tool_calls(self):
        """Tool calls from final continuation are preserved."""
        tc = ToolCall(name="file_read", arguments={"path": "/tmp/x"})
        inner = MagicMock()
        inner.chat.side_effect = [
            _make_response(content="Let me", stop_reason="length"),
            _make_response(content=" read.", tool_calls=[tc], stop_reason="tool_use"),
        ]

        llm = ResilientLLM(inner, max_continuations=3)
        result = llm.chat(_messages())

        assert result.content == "Let me read."
        assert result.tool_calls is not None
        assert result.tool_calls[0].name == "file_read"


# ── Context too long ──


class TestContextTooLong:
    def test_compress_and_retry(self):
        """Context-length error triggers compressor, then succeeds."""
        inner = MagicMock()
        exc = BadRequestError(
            "maximum context length exceeded",
            response=_mock_response(400),
            body={"error": {"message": "maximum context length exceeded", "type": "invalid_request_error"}},
        )
        inner.chat.side_effect = [
            exc,
            _make_response(content="ok after compression"),
        ]

        compressor = MagicMock(return_value=[{"role": "user", "content": "compressed"}])
        llm = ResilientLLM(inner, context_compressor=compressor, max_retries=3)
        result = llm.chat(_messages())

        assert result.content == "ok after compression"
        compressor.assert_called_once()
        # Second call should use compressed messages
        second_msgs = inner.chat.call_args_list[1][0][0]
        assert second_msgs == [{"role": "user", "content": "compressed"}]

    def test_no_compressor_raises(self):
        """Without a compressor, context-length error propagates."""
        inner = MagicMock()
        exc = BadRequestError(
            "context_length_exceeded",
            response=_mock_response(400),
            body={"error": {"message": "context_length_exceeded"}},
        )
        inner.chat.side_effect = exc

        llm = ResilientLLM(inner, context_compressor=None)
        with pytest.raises(BadRequestError):
            llm.chat(_messages())

    def test_non_context_bad_request_propagates(self):
        """Non-context-length BadRequestError should not trigger compression."""
        inner = MagicMock()
        exc = BadRequestError(
            "invalid model specified",
            response=_mock_response(400),
            body={"error": {"message": "invalid model specified"}},
        )
        inner.chat.side_effect = exc

        compressor = MagicMock()
        llm = ResilientLLM(inner, context_compressor=compressor, max_retries=3)
        with pytest.raises(BadRequestError):
            llm.chat(_messages())

        compressor.assert_not_called()


# ── Transient errors ──


class TestTransientErrors:
    @patch("app.resilient_llm.time.sleep")
    def test_rate_limit_retry(self, mock_sleep):
        """RateLimitError triggers retry with backoff."""
        inner = MagicMock()
        inner.chat.side_effect = [
            RateLimitError(
                "rate limit exceeded",
                response=_mock_response(429),
                body={"error": {"message": "rate limit exceeded"}},
            ),
            _make_response(content="recovered"),
        ]

        llm = ResilientLLM(inner, max_retries=3)
        result = llm.chat(_messages())

        assert result.content == "recovered"
        mock_sleep.assert_called_once()
        # Backoff: min(2^1, 10) = 2
        mock_sleep.assert_called_with(2)

    @patch("app.resilient_llm.time.sleep")
    def test_timeout_retry(self, mock_sleep):
        """APITimeoutError triggers retry."""
        inner = MagicMock()
        inner.chat.side_effect = [
            APITimeoutError(request=MagicMock()),
            _make_response(content="timeout recovered"),
        ]

        llm = ResilientLLM(inner, max_retries=3)
        result = llm.chat(_messages())

        assert result.content == "timeout recovered"

    @patch("app.resilient_llm.time.sleep")
    def test_connection_error_retry(self, mock_sleep):
        """APIConnectionError triggers retry."""
        inner = MagicMock()
        inner.chat.side_effect = [
            APIConnectionError(request=MagicMock()),
            _make_response(content="connection recovered"),
        ]

        llm = ResilientLLM(inner, max_retries=3)
        result = llm.chat(_messages())

        assert result.content == "connection recovered"

    @patch("app.resilient_llm.time.sleep")
    def test_exponential_backoff(self, mock_sleep):
        """Multiple retries use exponential backoff."""
        inner = MagicMock()
        inner.chat.side_effect = [
            RateLimitError("rate limit", response=_mock_response(429), body={}),
            RateLimitError("rate limit", response=_mock_response(429), body={}),
            _make_response(content="finally"),
        ]

        llm = ResilientLLM(inner, max_retries=3)
        result = llm.chat(_messages())

        assert result.content == "finally"
        assert mock_sleep.call_count == 2
        mock_sleep.assert_any_call(2)   # 2^1
        mock_sleep.assert_any_call(4)   # 2^2

    @patch("app.resilient_llm.time.sleep")
    def test_max_retries_exhausted(self, mock_sleep):
        """When retries are exhausted, the error propagates."""
        inner = MagicMock()
        exc = RateLimitError("rate limit", response=_mock_response(429), body={})
        inner.chat.side_effect = exc

        llm = ResilientLLM(inner, max_retries=2)
        with pytest.raises(RateLimitError):
            llm.chat(_messages())

        assert inner.chat.call_count == 3  # initial + 2 retries


# ── Happy path ──


class TestHappyPath:
    def test_normal_response_passes_through(self):
        """No errors — response returned as-is."""
        inner = MagicMock()
        inner.chat.return_value = _make_response(content="hello world")

        llm = ResilientLLM(inner)
        result = llm.chat(_messages())

        assert result.content == "hello world"
        assert inner.chat.call_count == 1

    def test_satisfies_llm_protocol(self):
        """ResilientLLM.chat() has the same signature as LLMProtocol."""
        inner = MagicMock()
        inner.chat.return_value = _make_response()
        llm = ResilientLLM(inner)

        # Should accept messages + optional tools
        result = llm.chat([{"role": "user", "content": "hi"}], tools=[{"type": "function", "function": {"name": "f"}}])
        assert result is not None
