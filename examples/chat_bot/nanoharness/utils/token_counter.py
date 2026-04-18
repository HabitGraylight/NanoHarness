from typing import Dict, List

# Rough approximation: ~4 characters per token for English text.
_CHARS_PER_TOKEN = 4


def count_tokens(text: str) -> int:
    """Estimate token count for a string."""
    return max(1, len(text) // _CHARS_PER_TOKEN)


def count_messages_tokens(messages: List[Dict]) -> int:
    """Estimate total token count across a list of message dicts."""
    total = 0
    for msg in messages:
        total += count_tokens(msg.get("content", ""))
        # Count tool call names/arguments if present
        for tc in msg.get("tool_calls") or []:
            total += count_tokens(tc.get("name", ""))
            total += count_tokens(str(tc.get("arguments", {})))
    return total
