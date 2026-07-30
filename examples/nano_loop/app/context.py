"""OpenAI-compatible context formatting isolated to the NanoLoop example."""

import json
from typing import Dict, List, Optional

from nanoharness.core.base import BaseContextManager
from nanoharness.core.schema import AgentMessage


class LoopContextManager(BaseContextManager):
    """In-memory messages with stable synthetic tool-call identifiers."""

    def __init__(self, system_prompt: Optional[str] = None):
        self._system_prompt = system_prompt
        self._messages: List[AgentMessage] = []
        self.reset()

    def add_message(self, msg: AgentMessage):
        self._messages.append(msg)

    def get_full_context(self) -> List[Dict]:
        result = []
        pending_ids: List[str] = []

        for message_index, message in enumerate(self._messages):
            data = {
                key: value
                for key, value in message.model_dump().items()
                if value is not None
            }
            role = data.get("role")

            if role == "assistant" and data.get("tool_calls"):
                pending_ids = []
                formatted = []
                for call_index, tool_call in enumerate(data["tool_calls"]):
                    call_id = f"call_{message_index}_{call_index}"
                    pending_ids.append(call_id)
                    formatted.append(
                        {
                            "id": call_id,
                            "type": "function",
                            "function": {
                                "name": tool_call["name"],
                                "arguments": json.dumps(
                                    tool_call["arguments"],
                                    ensure_ascii=False,
                                ),
                            },
                        }
                    )
                data["tool_calls"] = formatted
            elif role == "tool":
                data["tool_call_id"] = (
                    pending_ids.pop(0)
                    if pending_ids
                    else f"call_orphan_{message_index}"
                )

            result.append(data)
        return result

    def reset(self):
        self._messages = []
        if self._system_prompt:
            self._messages.append(
                AgentMessage(role="system", content=self._system_prompt)
            )
