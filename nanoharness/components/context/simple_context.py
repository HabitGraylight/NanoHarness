import json
from typing import Dict, List, Optional

from nanoharness.core.base import BaseContextManager
from nanoharness.core.schema import AgentMessage


class SimpleContextManager(BaseContextManager):
    """In-memory message list with an optional system prompt."""

    def __init__(self, system_prompt: Optional[str] = None):
        self._messages: List[AgentMessage] = []
        if system_prompt:
            self._messages.append(
                AgentMessage(role="system", content=system_prompt)
            )

    def add_message(self, msg: AgentMessage):
        self._messages.append(msg)

    def get_full_context(self) -> List[Dict]:
        return _format_messages(self._messages)

    def reset(self):
        self._messages.clear()


def _format_messages(messages: List[AgentMessage]) -> List[Dict]:
    """Convert internal messages to OpenAI-compatible API format.

    Handles two format mismatches:
    1. Assistant tool_calls: our ToolCall is {name, arguments} but the API
       expects {id, type:"function", function:{name, arguments:str}}
    2. Tool-role messages: need tool_call_id matching the preceding tool call
    """
    result = []
    pending_ids = []  # tool_call_ids from the last assistant message with tools
    call_counter = 0

    for msg in messages:
        d = msg.model_dump()

        # Drop None-valued fields
        cleaned = {k: v for k, v in d.items() if v is not None}
        role = cleaned.get("role", "")

        if role == "assistant" and "tool_calls" in cleaned:
            # Reformat tool_calls to OpenAI API shape
            pending_ids = []
            formatted_calls = []
            for tc in cleaned["tool_calls"]:
                call_id = tc.get("call_id")
                if not call_id:
                    call_id = f"call_{call_counter}"
                    call_counter += 1
                pending_ids.append(call_id)
                # arguments must be a JSON string in the API
                args = tc["arguments"]
                if isinstance(args, dict):
                    args = json.dumps(args, ensure_ascii=False)
                formatted_calls.append({
                    "id": call_id,
                    "type": "function",
                    "function": {
                        "name": tc["name"],
                        "arguments": args,
                    },
                })
            cleaned["tool_calls"] = formatted_calls

        elif role == "tool":
            # Prefer the engine/provider ID; infer one for v1 messages.
            explicit_id = cleaned.get("tool_call_id")
            if explicit_id:
                if explicit_id in pending_ids:
                    pending_ids.remove(explicit_id)
            elif pending_ids:
                cleaned["tool_call_id"] = pending_ids.pop(0)
            else:
                cleaned["tool_call_id"] = f"call_orphan_{call_counter}"
                call_counter += 1

        result.append(cleaned)

    return result
