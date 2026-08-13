"""Atomic conversation and turn persistence for NanoOpenClaw."""

from __future__ import annotations

import json
from pathlib import Path

from app.models import (
    ConversationExchange,
    ConversationRoute,
    ConversationState,
    GatewayTurnState,
    utc_now,
)


class ConversationConflictError(RuntimeError):
    pass


class ConversationStore:
    def __init__(self, path: str | Path):
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def resolve(self, route: ConversationRoute) -> ConversationState:
        conversations = self._load_all()
        existing = conversations.get(route.stable_conversation_id)
        if existing is not None:
            if existing.route != route:
                raise ConversationConflictError(
                    "conversation identity was reused for a different route"
                )
            return existing.model_copy(deep=True)
        state = ConversationState(
            conversation_id=route.stable_conversation_id,
            session_id=route.stable_session_id,
            route=route.model_copy(deep=True),
        )
        conversations[state.conversation_id] = state
        self._save_all(conversations)
        return state.model_copy(deep=True)

    def get(self, conversation_id: str) -> ConversationState | None:
        state = self._load_all().get(conversation_id)
        return state.model_copy(deep=True) if state is not None else None

    def commit(
        self,
        conversation_id: str,
        exchange: ConversationExchange,
    ) -> ConversationState:
        conversations = self._load_all()
        state = conversations.get(conversation_id)
        if state is None:
            raise KeyError(f"conversation not found: {conversation_id}")
        existing = next(
            (item for item in state.exchanges if item.turn_id == exchange.turn_id),
            None,
        )
        if existing is not None:
            comparable_existing = existing.model_dump(exclude={"completed_at"})
            comparable_new = exchange.model_dump(exclude={"completed_at"})
            if comparable_existing != comparable_new:
                raise ConversationConflictError(
                    "turn was already committed with a different exchange"
                )
            return state.model_copy(deep=True)
        state.exchanges.append(exchange.model_copy(deep=True))
        state.updated_at = utc_now()
        conversations[conversation_id] = state
        self._save_all(conversations)
        return state.model_copy(deep=True)

    def _load_all(self) -> dict[str, ConversationState]:
        if not self.path.exists():
            return {}
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("conversation store must contain an object")
        return {
            key: ConversationState.model_validate(value)
            for key, value in payload.items()
        }

    def _save_all(self, conversations: dict[str, ConversationState]) -> None:
        _atomic_json(
            self.path,
            {
                key: state.model_dump(mode="json")
                for key, state in sorted(conversations.items())
            },
        )


class GatewayTurnStore:
    def __init__(self, path: str | Path):
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def exists(self) -> bool:
        return self.path.is_file()

    def load(self) -> GatewayTurnState:
        return GatewayTurnState.model_validate_json(
            self.path.read_text(encoding="utf-8")
        )

    def save(self, state: GatewayTurnState) -> None:
        state.updated_at = utc_now()
        _atomic_json(self.path, state.model_dump(mode="json"))


def _atomic_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(path)
