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
        return [msg.model_dump() for msg in self._messages]

    def reset(self):
        self._messages.clear()
