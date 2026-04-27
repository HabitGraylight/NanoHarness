"""Resilient LLM wrapper — error recovery for the coding agent.

Handles three failure modes at the LLM call boundary:
  1. max_tokens truncation  → inject continuation prompt, accumulate content
  2. prompt too long        → compress context via callback, retry with fresh messages
  3. transient API errors   → exponential backoff retry (rate limit, timeout, connection)

Satisfies LLMProtocol — drop-in replacement for any LLM adapter.
"""

import logging
import time
from typing import Any, Callable, Dict, List, Optional

from openai import (
    APIConnectionError,
    APITimeoutError,
    BadRequestError,
    RateLimitError,
)

from nanoharness.core.schema import LLMResponse, ToolCall

from app.adapters import DetailedLLMResponse

logger = logging.getLogger(__name__)

# ── Defaults ──

_MAX_RETRIES = 3           # transient error retries
_MAX_CONTINUATIONS = 3     # max_tokens continuation rounds
_BACKOFF_BASE = 2          # seconds
_BACKOFF_CAP = 10          # seconds
_CONTINUATION_PROMPT = (
    "[Your previous response was truncated. "
    "Continue exactly from where you left off. Do not repeat what you already said.]"
)


class ResilientLLM:
    """Wraps any LLMProtocol with error recovery.

    Args:
        inner:              The raw LLM adapter (e.g. OpenAIAdapter).
        context_compressor: Callback that compresses context and returns fresh messages.
                            Called when the API rejects a request for exceeding context length.
        max_retries:        Max retry attempts for transient errors (rate limit, timeout).
        max_continuations:  Max continuation rounds when stop_reason == "length".
    """

    def __init__(
        self,
        inner,
        context_compressor: Optional[Callable[[], List[Dict[str, Any]]]] = None,
        max_retries: int = _MAX_RETRIES,
        max_continuations: int = _MAX_CONTINUATIONS,
    ):
        self._inner = inner
        self._compress = context_compressor
        self._max_retries = max_retries
        self._max_continuations = max_continuations

    def chat(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
    ) -> LLMResponse:
        accumulated_content = ""
        accumulated_tool_calls: list[ToolCall] = []
        current_messages = list(messages)
        continuations = 0
        retries = 0

        while True:
            try:
                response = self._inner.chat(current_messages, tools=tools)

            except BadRequestError as e:
                # ── Case 2: prompt too long ──
                if _is_context_length_error(e) and self._compress and retries < self._max_retries:
                    retries += 1
                    logger.info("Context too long (attempt %d) — compressing context", retries)
                    current_messages = self._compress()
                    continue
                raise

            except (RateLimitError, APITimeoutError, APIConnectionError) as e:
                # ── Case 3: transient error ──
                if retries < self._max_retries:
                    retries += 1
                    delay = min(_BACKOFF_BASE ** retries, _BACKOFF_CAP)
                    logger.info(
                        "Transient error %s (attempt %d) — retrying in %.1fs",
                        type(e).__name__, retries, delay,
                    )
                    time.sleep(delay)
                    continue
                raise

            # ── Case 1: max_tokens truncation ──
            stop = getattr(response, "stop_reason", "end_turn")

            if stop == "length" and continuations < self._max_continuations:
                continuations += 1
                accumulated_content += response.content or ""
                accumulated_tool_calls.extend(response.tool_calls or [])

                # Inject continuation prompt
                current_messages.append({"role": "assistant", "content": response.content})
                current_messages.append({"role": "user", "content": _CONTINUATION_PROMPT})
                logger.info("max_tokens reached (continuation %d) — requesting more", continuations)
                continue

            # ── Final: assemble result ──
            if accumulated_content or accumulated_tool_calls:
                accumulated_content += response.content or ""
                accumulated_tool_calls.extend(response.tool_calls or [])
                return LLMResponse(
                    content=accumulated_content,
                    tool_calls=accumulated_tool_calls or None,
                )

            return response


def _is_context_length_error(exc: BadRequestError) -> bool:
    """Check if a BadRequestError is specifically a context length exceeded error."""
    message = str(exc).lower()
    return "context_length" in message or "maximum context length" in message
