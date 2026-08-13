"""Normalize channel, scheduler, background, and operator inputs as wakeups."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping

from nanoharness.extensions.channels import InboxRecord

from app.models import (
    ConversationRoute,
    WakeupEnvelope,
    WakeupSource,
    WakeupTrust,
    content_sha256,
    stable_wakeup_id,
    utc_now,
)


def channel_wakeup(record: InboxRecord) -> WakeupEnvelope:
    envelope = record.envelope
    return WakeupEnvelope(
        wakeup_id=record.id,
        source=WakeupSource.CHANNEL,
        trust=WakeupTrust.UNTRUSTED,
        source_id=record.id,
        route=ConversationRoute.from_envelope(envelope),
        content=envelope.content,
        channel_inbox_id=record.id,
        occurred_at=envelope.received_at,
        metadata={
            "external_message_id": envelope.message_id,
            "channel_metadata": envelope.metadata,
        },
    )


def schedule_wakeup(notice: Mapping[str, Any]) -> WakeupEnvelope:
    schedule_id = _required_int(notice, "schedule_id")
    fire_count = _required_int(notice, "fire_count")
    prompt = _required_text(notice, "prompt")
    metadata = _metadata(notice)
    route_payload = metadata.get("route")
    if not isinstance(route_payload, Mapping):
        raise ValueError("scheduled wakeup metadata requires a route object")
    source_id = f"schedule:{schedule_id}:{fire_count}"
    return WakeupEnvelope(
        wakeup_id=stable_wakeup_id(WakeupSource.SCHEDULE, source_id),
        source=WakeupSource.SCHEDULE,
        trust=WakeupTrust.TRUSTED_SYSTEM,
        source_id=source_id,
        route=ConversationRoute.model_validate(route_payload),
        content=prompt,
        occurred_at=_timestamp(notice.get("fired_at")),
        metadata={
            **metadata,
            "schedule_id": schedule_id,
            "fire_count": fire_count,
            "schedule_status": notice.get("status"),
        },
    )


def background_wakeup(
    notice: Mapping[str, Any],
    route: ConversationRoute,
    *,
    source_instance: str = "background",
) -> WakeupEnvelope:
    task_id = _required_int(notice, "task_id")
    status = _required_text(notice, "status")
    message = _required_text(notice, "message")
    finished_at = notice.get("finished_at")
    identity_time = str(
        finished_at
        if finished_at is not None
        else "content-" + content_sha256(message)[:16]
    )
    source_id = f"{source_instance}:{task_id}:{status}:{identity_time}"
    return WakeupEnvelope(
        wakeup_id=stable_wakeup_id(WakeupSource.BACKGROUND, source_id),
        source=WakeupSource.BACKGROUND,
        trust=WakeupTrust.TRUSTED_SYSTEM,
        source_id=source_id,
        route=route,
        content=message,
        occurred_at=_timestamp(finished_at),
        metadata={
            "task_id": task_id,
            "status": status,
            "exit_code": notice.get("exit_code"),
            "log_path": notice.get("log_path"),
            "source_instance": source_instance,
        },
    )


def manual_wakeup(
    content: str,
    route: ConversationRoute,
    *,
    event_id: str,
    metadata: Mapping[str, Any] | None = None,
) -> WakeupEnvelope:
    source_id = f"operator:{event_id}"
    return WakeupEnvelope(
        wakeup_id=stable_wakeup_id(WakeupSource.MANUAL, source_id),
        source=WakeupSource.MANUAL,
        trust=WakeupTrust.OPERATOR,
        source_id=source_id,
        route=route,
        content=content,
        metadata=dict(metadata or {}),
    )


def _metadata(notice: Mapping[str, Any]) -> dict[str, Any]:
    value = notice.get("metadata") or {}
    if not isinstance(value, Mapping):
        raise ValueError("notification metadata must be an object")
    return dict(value)


def _required_text(notice: Mapping[str, Any], key: str) -> str:
    value = notice.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"notification requires non-empty {key}")
    return value


def _required_int(notice: Mapping[str, Any], key: str) -> int:
    value = notice.get(key)
    if isinstance(value, bool):
        raise ValueError(f"notification requires integer {key}")
    try:
        return int(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"notification requires integer {key}") from error


def _timestamp(value: Any) -> datetime:
    if value is None:
        return utc_now()
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return datetime.fromtimestamp(value, tz=timezone.utc)
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("notification timestamp must include a timezone")
        return value.astimezone(timezone.utc)
    if isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError("notification timestamp must include a timezone")
        return parsed.astimezone(timezone.utc)
    raise ValueError("unsupported notification timestamp")
