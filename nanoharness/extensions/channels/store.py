"""Atomic durable inbox/outbox state for channel gateways."""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Optional
from uuid import uuid4

from nanoharness.extensions.channels.models import (
    ChannelStoreState,
    DeliveryAttempt,
    DeliveryAttemptStatus,
    DeliveryReceipt,
    InboxRecord,
    InboxStatus,
    InboundEnvelope,
    OutboundEnvelope,
    OutboxRecord,
    OutboxStatus,
    stable_inbox_id,
    stable_outbox_id,
    utc_now,
)


class ChannelStoreError(RuntimeError):
    pass


class ChannelConflictError(ChannelStoreError):
    pass


class ChannelStateTransitionError(ChannelStoreError):
    pass


class DurableChannelStore:
    """A small single-process store with atomic crash-readable snapshots."""

    def __init__(
        self,
        persist_path: str,
        *,
        events_path: Optional[str] = None,
        claim_lease_seconds: float = 300.0,
    ):
        if claim_lease_seconds <= 0:
            raise ValueError("claim_lease_seconds must be positive")
        self.path = Path(persist_path).resolve()
        self.events_path = (
            Path(events_path).resolve()
            if events_path
            else self.path.with_name("events.jsonl")
        )
        if self.path == self.events_path:
            raise ValueError("persist_path and events_path must be different")
        if self.path.exists() and self.path.is_dir():
            raise ValueError("persist_path must be a file")
        if self.events_path.exists() and self.events_path.is_dir():
            raise ValueError("events_path must be a file")
        self.claim_lease_seconds = claim_lease_seconds
        self._lock = threading.RLock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.events_path.parent.mkdir(parents=True, exist_ok=True)
        self._state = self._load() if self.path.exists() else ChannelStoreState()

    def snapshot(self) -> ChannelStoreState:
        with self._lock:
            return self._state.model_copy(deep=True)

    def ingest(self, envelope: InboundEnvelope) -> tuple[InboxRecord, bool]:
        inbox_id = stable_inbox_id(envelope)
        with self._lock:
            existing = self._state.inbox.get(inbox_id)
            if existing is not None:
                if (
                    existing.dedupe_key != envelope.dedupe_key
                    or existing.payload_fingerprint != envelope.payload_fingerprint
                ):
                    raise ChannelConflictError(
                        "inbound message identity was reused with a different payload"
                    )
                self._emit(
                    "inbox.duplicate",
                    inbox_id=inbox_id,
                    channel=envelope.channel,
                )
                return existing.model_copy(deep=True), False

            now = utc_now()
            record = InboxRecord(
                id=inbox_id,
                dedupe_key=envelope.dedupe_key,
                payload_fingerprint=envelope.payload_fingerprint,
                envelope=envelope.model_copy(deep=True),
                created_at=now,
                updated_at=now,
            )
            self._state.inbox[inbox_id] = record
            self._save()
            self._emit("inbox.received", inbox_id=inbox_id, channel=envelope.channel)
            return record.model_copy(deep=True), True

    def get_inbox(self, inbox_id: str) -> Optional[InboxRecord]:
        with self._lock:
            record = self._state.inbox.get(inbox_id)
            return record.model_copy(deep=True) if record is not None else None

    def list_inbox(
        self,
        status: Optional[InboxStatus | str] = None,
    ) -> list[InboxRecord]:
        expected = InboxStatus(status) if status is not None else None
        with self._lock:
            records = self._ordered_inbox(self._state.inbox.values())
            if expected is not None:
                records = [record for record in records if record.status == expected]
            return [record.model_copy(deep=True) for record in records]

    def claim_next(
        self,
        worker_id: str,
        *,
        lease_seconds: Optional[float] = None,
        now: Optional[datetime] = None,
    ) -> Optional[InboxRecord]:
        if not worker_id or not worker_id.strip():
            raise ValueError("worker_id is required")
        lease = self.claim_lease_seconds if lease_seconds is None else lease_seconds
        if lease <= 0:
            raise ValueError("lease_seconds must be positive")
        current = _normalize_now(now)
        with self._lock:
            recovered = self._recover_expired_locked(current)
            received = self._ordered_inbox(
                record
                for record in self._state.inbox.values()
                if record.status == InboxStatus.RECEIVED
            )
            if not received:
                if recovered:
                    self._save()
                    self._emit("inbox.claims_recovered", count=recovered)
                return None
            record = received[0]
            record.status = InboxStatus.CLAIMED
            record.claim_token = f"claim_{uuid4().hex}"
            record.claim_owner = worker_id
            record.claimed_at = current
            record.lease_expires_at = current + timedelta(seconds=lease)
            record.claim_count += 1
            record.updated_at = current
            self._save()
            if recovered:
                self._emit("inbox.claims_recovered", count=recovered)
            self._emit(
                "inbox.claimed",
                inbox_id=record.id,
                worker_id=worker_id,
                claim_count=record.claim_count,
            )
            return record.model_copy(deep=True)

    def complete_inbox(
        self,
        inbox_id: str,
        claim_token: str,
        *,
        run_id: str,
        now: Optional[datetime] = None,
    ) -> InboxRecord:
        if not run_id:
            raise ValueError("run_id is required")
        current = _normalize_now(now)
        with self._lock:
            record = self._require_inbox(inbox_id)
            self._require_claim(record, claim_token, current)
            record.status = InboxStatus.COMPLETED
            record.run_id = run_id
            self._clear_claim(record)
            record.last_error = None
            record.updated_at = current
            self._save()
            self._emit("inbox.completed", inbox_id=inbox_id, run_id=run_id)
            return record.model_copy(deep=True)

    def fail_inbox(
        self,
        inbox_id: str,
        claim_token: str,
        *,
        error: str,
        retryable: bool,
        now: Optional[datetime] = None,
    ) -> InboxRecord:
        current = _normalize_now(now)
        with self._lock:
            record = self._require_inbox(inbox_id)
            self._require_claim(record, claim_token, current)
            record.status = (
                InboxStatus.RECEIVED if retryable else InboxStatus.FAILED
            )
            record.last_error = _bounded_error(error)
            self._clear_claim(record)
            record.updated_at = current
            self._save()
            self._emit(
                "inbox.retry" if retryable else "inbox.failed",
                inbox_id=inbox_id,
            )
            return record.model_copy(deep=True)

    def recover_expired_claims(
        self,
        *,
        now: Optional[datetime] = None,
    ) -> int:
        current = _normalize_now(now)
        with self._lock:
            recovered = self._recover_expired_locked(current)
            if recovered:
                self._save()
                self._emit("inbox.claims_recovered", count=recovered)
            return recovered

    def queue_outbound(
        self,
        envelope: OutboundEnvelope,
        *,
        idempotency_key: str,
    ) -> tuple[OutboxRecord, bool]:
        outbox_id = stable_outbox_id(idempotency_key)
        with self._lock:
            existing = self._state.outbox.get(outbox_id)
            if existing is not None:
                if (
                    existing.idempotency_key != idempotency_key
                    or existing.payload_fingerprint != envelope.payload_fingerprint
                ):
                    raise ChannelConflictError(
                        "outbound idempotency key was reused with a different payload"
                    )
                self._emit(
                    "outbox.duplicate",
                    outbox_id=outbox_id,
                    channel=envelope.channel,
                )
                return existing.model_copy(deep=True), False

            now = utc_now()
            record = OutboxRecord(
                id=outbox_id,
                idempotency_key=idempotency_key,
                payload_fingerprint=envelope.payload_fingerprint,
                envelope=envelope.model_copy(deep=True),
                created_at=now,
                updated_at=now,
            )
            self._state.outbox[outbox_id] = record
            self._save()
            self._emit(
                "outbox.pending",
                outbox_id=outbox_id,
                channel=envelope.channel,
            )
            return record.model_copy(deep=True), True

    def get_outbox(self, outbox_id: str) -> Optional[OutboxRecord]:
        with self._lock:
            record = self._state.outbox.get(outbox_id)
            return record.model_copy(deep=True) if record is not None else None

    def list_outbox(
        self,
        status: Optional[OutboxStatus | str] = None,
    ) -> list[OutboxRecord]:
        expected = OutboxStatus(status) if status is not None else None
        with self._lock:
            records = sorted(
                self._state.outbox.values(),
                key=lambda record: (record.created_at, record.id),
            )
            if expected is not None:
                records = [record for record in records if record.status == expected]
            return [record.model_copy(deep=True) for record in records]

    def approve_outbox(self, outbox_id: str) -> OutboxRecord:
        with self._lock:
            record = self._require_outbox(outbox_id)
            if record.status == OutboxStatus.APPROVED:
                return record.model_copy(deep=True)
            self._require_outbox_status(record, OutboxStatus.PENDING)
            record.status = OutboxStatus.APPROVED
            record.updated_at = utc_now()
            self._save()
            self._emit("outbox.approved", outbox_id=outbox_id)
            return record.model_copy(deep=True)

    def reject_outbox(self, outbox_id: str, *, reason: str = "") -> OutboxRecord:
        with self._lock:
            record = self._require_outbox(outbox_id)
            if record.status == OutboxStatus.REJECTED:
                return record.model_copy(deep=True)
            self._require_outbox_status(record, OutboxStatus.PENDING)
            record.status = OutboxStatus.REJECTED
            record.last_error = _bounded_error(reason) if reason else None
            record.updated_at = utc_now()
            self._save()
            self._emit("outbox.rejected", outbox_id=outbox_id)
            return record.model_copy(deep=True)

    def begin_delivery(self, outbox_id: str) -> OutboxRecord:
        with self._lock:
            record = self._require_outbox(outbox_id)
            self._require_outbox_status(record, OutboxStatus.APPROVED)
            token = f"delivery_{uuid4().hex}"
            now = utc_now()
            record.status = OutboxStatus.SENDING
            record.delivery_token = token
            record.last_error = None
            record.attempts.append(
                DeliveryAttempt(
                    number=len(record.attempts) + 1,
                    token=token,
                    started_at=now,
                )
            )
            record.updated_at = now
            self._save()
            self._emit(
                "outbox.sending",
                outbox_id=outbox_id,
                attempt=len(record.attempts),
            )
            return record.model_copy(deep=True)

    def mark_delivery_sent(
        self,
        outbox_id: str,
        delivery_token: str,
        receipt: DeliveryReceipt,
    ) -> OutboxRecord:
        with self._lock:
            record = self._require_delivery(outbox_id, delivery_token)
            if receipt.idempotency_key != record.idempotency_key:
                raise ChannelConflictError("delivery receipt idempotency key mismatch")
            if receipt.channel != record.envelope.channel:
                raise ChannelConflictError("delivery receipt channel mismatch")
            attempt = record.attempts[-1]
            attempt.status = DeliveryAttemptStatus.SENT
            attempt.finished_at = utc_now()
            attempt.external_delivery_id = receipt.external_delivery_id
            record.status = OutboxStatus.SENT
            record.external_delivery_id = receipt.external_delivery_id
            record.delivery_token = None
            record.last_error = None
            record.updated_at = attempt.finished_at
            self._save()
            self._emit(
                "outbox.sent",
                outbox_id=outbox_id,
                external_delivery_id=receipt.external_delivery_id,
            )
            return record.model_copy(deep=True)

    def mark_delivery_failed(
        self,
        outbox_id: str,
        delivery_token: str,
        *,
        error: str,
    ) -> OutboxRecord:
        with self._lock:
            record = self._require_delivery(outbox_id, delivery_token)
            attempt = record.attempts[-1]
            attempt.status = DeliveryAttemptStatus.FAILED
            attempt.finished_at = utc_now()
            attempt.error = _bounded_error(error)
            record.status = OutboxStatus.FAILED
            record.delivery_token = None
            record.last_error = attempt.error
            record.updated_at = attempt.finished_at
            self._save()
            self._emit("outbox.failed", outbox_id=outbox_id)
            return record.model_copy(deep=True)

    def retry_outbox(self, outbox_id: str) -> OutboxRecord:
        with self._lock:
            record = self._require_outbox(outbox_id)
            self._require_outbox_status(record, OutboxStatus.FAILED)
            record.status = OutboxStatus.APPROVED
            record.updated_at = utc_now()
            self._save()
            self._emit("outbox.retry", outbox_id=outbox_id)
            return record.model_copy(deep=True)

    def recover_sending(self) -> int:
        with self._lock:
            records = [
                record
                for record in self._state.outbox.values()
                if record.status == OutboxStatus.SENDING
            ]
            if not records:
                return 0
            now = utc_now()
            for record in records:
                if record.attempts:
                    attempt = record.attempts[-1]
                    attempt.status = DeliveryAttemptStatus.RECOVERED
                    attempt.finished_at = now
                    attempt.error = "delivery state recovered after interruption"
                record.status = OutboxStatus.APPROVED
                record.delivery_token = None
                record.last_error = "delivery state recovered after interruption"
                record.updated_at = now
            self._save()
            self._emit("outbox.sending_recovered", count=len(records))
            return len(records)

    def _load(self) -> ChannelStoreState:
        try:
            return ChannelStoreState.model_validate_json(
                self.path.read_text(encoding="utf-8")
            )
        except Exception as error:
            raise ChannelStoreError(
                f"invalid durable channel state: {self.path}"
            ) from error

    def _save(self) -> None:
        self._state = ChannelStoreState.model_validate(
            self._state.model_dump(mode="python")
        )
        temporary = self.path.with_name(self.path.name + ".tmp")
        payload = self._state.model_dump_json(indent=2)
        with temporary.open("w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, self.path)

    def _emit(self, event: str, **data: Any) -> None:
        record = {"event": event, "at": utc_now().isoformat(), **data}
        with self.events_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")

    def _recover_expired_locked(self, now: datetime) -> int:
        recovered = 0
        for record in self._state.inbox.values():
            if (
                record.status == InboxStatus.CLAIMED
                and record.lease_expires_at is not None
                and record.lease_expires_at <= now
            ):
                record.status = InboxStatus.RECEIVED
                record.last_error = "claim lease expired"
                self._clear_claim(record)
                record.updated_at = now
                recovered += 1
        return recovered

    @staticmethod
    def _ordered_inbox(records: Iterable[InboxRecord]) -> list[InboxRecord]:
        return sorted(
            records,
            key=lambda record: (
                record.envelope.received_at,
                record.created_at,
                record.id,
            ),
        )

    def _require_inbox(self, inbox_id: str) -> InboxRecord:
        record = self._state.inbox.get(inbox_id)
        if record is None:
            raise KeyError(f"inbox record not found: {inbox_id}")
        return record

    def _require_outbox(self, outbox_id: str) -> OutboxRecord:
        record = self._state.outbox.get(outbox_id)
        if record is None:
            raise KeyError(f"outbox record not found: {outbox_id}")
        return record

    @staticmethod
    def _require_claim(
        record: InboxRecord,
        claim_token: str,
        now: datetime,
    ) -> None:
        if record.status != InboxStatus.CLAIMED:
            raise ChannelStateTransitionError(
                f"inbox record {record.id} is {record.status.value}, not claimed"
            )
        if not claim_token or record.claim_token != claim_token:
            raise ChannelStateTransitionError("stale or invalid inbox claim token")
        if record.lease_expires_at is None or record.lease_expires_at <= now:
            raise ChannelStateTransitionError("inbox claim lease has expired")

    @staticmethod
    def _clear_claim(record: InboxRecord) -> None:
        record.claim_token = None
        record.claim_owner = None
        record.claimed_at = None
        record.lease_expires_at = None

    @staticmethod
    def _require_outbox_status(
        record: OutboxRecord,
        expected: OutboxStatus,
    ) -> None:
        if record.status != expected:
            raise ChannelStateTransitionError(
                f"outbox record {record.id} is {record.status.value}, "
                f"not {expected.value}"
            )

    def _require_delivery(
        self,
        outbox_id: str,
        delivery_token: str,
    ) -> OutboxRecord:
        record = self._require_outbox(outbox_id)
        self._require_outbox_status(record, OutboxStatus.SENDING)
        if not delivery_token or record.delivery_token != delivery_token:
            raise ChannelStateTransitionError("stale or invalid delivery token")
        return record


def _normalize_now(value: Optional[datetime]) -> datetime:
    if value is None:
        return utc_now()
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("now must include a timezone")
    return value.astimezone(timezone.utc)


def _bounded_error(error: str) -> str:
    text = str(error)
    return text[:2000]
