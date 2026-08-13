"""Atomic conversation and turn persistence for NanoOpenClaw."""

from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
from uuid import uuid4

from app.models import (
    ConversationExchange,
    ConversationRoute,
    ConversationState,
    GatewayTurnState,
    WakeupEnvelope,
    WakeupRecord,
    WakeupStatus,
    utc_now,
)


class ConversationConflictError(RuntimeError):
    pass


class WakeupConflictError(RuntimeError):
    pass


class WakeupTransitionError(RuntimeError):
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


class WakeupStore:
    """Durable application-owned queue for normalized host wakeups."""

    def __init__(self, path: str | Path, *, claim_lease_seconds: float = 300.0):
        if claim_lease_seconds <= 0:
            raise ValueError("claim_lease_seconds must be positive")
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.claim_lease_seconds = claim_lease_seconds
        self._lock = threading.RLock()

    def ingest(self, envelope: WakeupEnvelope) -> tuple[WakeupRecord, bool]:
        with self._lock:
            records = self._load_all()
            existing = records.get(envelope.wakeup_id)
            if existing is not None:
                if existing.payload_fingerprint != envelope.payload_fingerprint:
                    raise WakeupConflictError(
                        "wakeup identity was reused with a different payload"
                    )
                return existing.model_copy(deep=True), False
            record = WakeupRecord(
                envelope=envelope.model_copy(deep=True),
                payload_fingerprint=envelope.payload_fingerprint,
            )
            records[record.id] = record
            self._save_all(records)
            return record.model_copy(deep=True), True

    def get(self, wakeup_id: str) -> WakeupRecord | None:
        with self._lock:
            record = self._load_all().get(wakeup_id)
            return record.model_copy(deep=True) if record is not None else None

    def list(self, status: WakeupStatus | str | None = None) -> list[WakeupRecord]:
        expected = WakeupStatus(status) if status is not None else None
        with self._lock:
            records = self._ordered(self._load_all().values())
            if expected is not None:
                records = [record for record in records if record.status == expected]
            return [record.model_copy(deep=True) for record in records]

    def claim_next(
        self,
        worker_id: str,
        *,
        lease_seconds: Optional[float] = None,
        now: Optional[datetime] = None,
    ) -> WakeupRecord | None:
        current = _normalize_now(now)
        lease = self._lease(worker_id, lease_seconds)
        with self._lock:
            records = self._load_all()
            self._recover_locked(records, current)
            pending = self._ordered(
                record
                for record in records.values()
                if record.status == WakeupStatus.PENDING
            )
            if not pending:
                self._save_all(records)
                return None
            record = pending[0]
            self._claim(record, worker_id, current, lease)
            self._save_all(records)
            return record.model_copy(deep=True)

    def claim(
        self,
        wakeup_id: str,
        worker_id: str,
        *,
        lease_seconds: Optional[float] = None,
        now: Optional[datetime] = None,
    ) -> WakeupRecord:
        current = _normalize_now(now)
        lease = self._lease(worker_id, lease_seconds)
        with self._lock:
            records = self._load_all()
            record = self._require(records, wakeup_id)
            if (
                record.status == WakeupStatus.CLAIMED
                and record.lease_expires_at is not None
                and record.lease_expires_at <= current
            ):
                self._clear_claim(record)
                record.status = WakeupStatus.PENDING
            if record.status != WakeupStatus.PENDING:
                raise WakeupTransitionError(
                    f"wakeup {wakeup_id} is {record.status.value}, not pending"
                )
            self._claim(record, worker_id, current, lease)
            self._save_all(records)
            return record.model_copy(deep=True)

    def complete(
        self,
        wakeup_id: str,
        claim_token: str,
        *,
        run_id: str,
        now: Optional[datetime] = None,
    ) -> WakeupRecord:
        if not run_id:
            raise ValueError("run_id is required")
        current = _normalize_now(now)
        with self._lock:
            records = self._load_all()
            record = self._require_claim(records, wakeup_id, claim_token, current)
            record.status = WakeupStatus.COMPLETED
            record.run_id = run_id
            record.last_error = ""
            record.updated_at = current
            self._clear_claim(record)
            self._save_all(records)
            return record.model_copy(deep=True)

    def fail(
        self,
        wakeup_id: str,
        claim_token: str,
        *,
        error: str,
        retryable: bool,
        now: Optional[datetime] = None,
    ) -> WakeupRecord:
        current = _normalize_now(now)
        with self._lock:
            records = self._load_all()
            record = self._require_claim(records, wakeup_id, claim_token, current)
            record.status = (
                WakeupStatus.PENDING if retryable else WakeupStatus.FAILED
            )
            record.last_error = str(error)[:2000]
            record.updated_at = current
            self._clear_claim(record)
            self._save_all(records)
            return record.model_copy(deep=True)

    def snapshot(self) -> dict[str, WakeupRecord]:
        with self._lock:
            return {
                key: value.model_copy(deep=True)
                for key, value in self._load_all().items()
            }

    def recover_expired(self, *, now: Optional[datetime] = None) -> int:
        current = _normalize_now(now)
        with self._lock:
            records = self._load_all()
            recovered = self._recover_locked(records, current)
            if recovered:
                self._save_all(records)
            return recovered

    def _load_all(self) -> dict[str, WakeupRecord]:
        if not self.path.exists():
            return {}
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("wakeup store must contain an object")
        records = {
            key: WakeupRecord.model_validate(value)
            for key, value in payload.items()
        }
        if any(key != record.id for key, record in records.items()):
            raise ValueError("wakeup store index does not match record identity")
        return records

    def _save_all(self, records: dict[str, WakeupRecord]) -> None:
        _atomic_json(
            self.path,
            {
                key: record.model_dump(mode="json")
                for key, record in sorted(records.items())
            },
        )

    def _lease(self, worker_id: str, lease_seconds: Optional[float]) -> float:
        if not worker_id or not worker_id.strip():
            raise ValueError("worker_id is required")
        lease = self.claim_lease_seconds if lease_seconds is None else lease_seconds
        if lease <= 0:
            raise ValueError("lease_seconds must be positive")
        return lease

    @staticmethod
    def _ordered(records) -> list[WakeupRecord]:
        return sorted(records, key=lambda record: (record.created_at, record.id))

    @staticmethod
    def _require(
        records: dict[str, WakeupRecord],
        wakeup_id: str,
    ) -> WakeupRecord:
        record = records.get(wakeup_id)
        if record is None:
            raise KeyError(f"wakeup not found: {wakeup_id}")
        return record

    def _require_claim(
        self,
        records: dict[str, WakeupRecord],
        wakeup_id: str,
        claim_token: str,
        current: datetime,
    ) -> WakeupRecord:
        record = self._require(records, wakeup_id)
        if record.status != WakeupStatus.CLAIMED:
            raise WakeupTransitionError(f"wakeup {wakeup_id} is not claimed")
        if not claim_token or record.claim_token != claim_token:
            raise WakeupTransitionError("stale or invalid wakeup claim token")
        if record.lease_expires_at is None or record.lease_expires_at <= current:
            raise WakeupTransitionError("wakeup claim lease expired")
        return record

    @staticmethod
    def _claim(
        record: WakeupRecord,
        worker_id: str,
        current: datetime,
        lease_seconds: float,
    ) -> None:
        record.status = WakeupStatus.CLAIMED
        record.claim_token = f"wake_claim_{uuid4().hex}"
        record.claim_owner = worker_id
        record.claimed_at = current
        record.lease_expires_at = current + timedelta(seconds=lease_seconds)
        record.claim_count += 1
        record.updated_at = current

    @staticmethod
    def _clear_claim(record: WakeupRecord) -> None:
        record.claim_token = None
        record.claim_owner = None
        record.claimed_at = None
        record.lease_expires_at = None

    def _recover_locked(
        self,
        records: dict[str, WakeupRecord],
        current: datetime,
    ) -> int:
        recovered = 0
        for record in records.values():
            if (
                record.status == WakeupStatus.CLAIMED
                and record.lease_expires_at is not None
                and record.lease_expires_at <= current
            ):
                record.status = WakeupStatus.PENDING
                self._clear_claim(record)
                record.updated_at = current
                recovered += 1
        return recovered


def _atomic_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(path)


def _normalize_now(value: Optional[datetime]) -> datetime:
    if value is None:
        return utc_now()
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("now must include a timezone")
    return value.astimezone(timezone.utc)
