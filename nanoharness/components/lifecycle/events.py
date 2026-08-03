"""Composable event fan-out, redaction, persistence, and console sinks."""

import json
from pathlib import Path
from threading import RLock
from typing import Any, Callable, Iterable, Optional, Union

from nanoharness.core.base import EventSinkProtocol
from nanoharness.core.schema import HarnessEvent


EventSubscriber = Union[EventSinkProtocol, Callable[[HarnessEvent], None]]


class EventBus:
    """Synchronous real-time fan-out with failure isolation per subscriber."""

    def __init__(self, subscribers: Optional[Iterable[EventSubscriber]] = None):
        self._subscribers = list(subscribers or [])
        self._lock = RLock()
        self.errors: list[str] = []

    def subscribe(self, subscriber: EventSubscriber) -> Callable[[], None]:
        with self._lock:
            self._subscribers.append(subscriber)

        def unsubscribe() -> None:
            with self._lock:
                if subscriber in self._subscribers:
                    self._subscribers.remove(subscriber)

        return unsubscribe

    def publish(self, event: HarnessEvent) -> None:
        with self._lock:
            subscribers = list(self._subscribers)

        for subscriber in subscribers:
            try:
                if hasattr(subscriber, "publish"):
                    subscriber.publish(event)
                else:
                    subscriber(event)
            except Exception as exc:
                self.errors.append(
                    f"{type(subscriber).__name__}: {type(exc).__name__}: {exc}"
                )


class RedactingEventSink:
    """Remove values under configured sensitive keys before forwarding."""

    DEFAULT_SENSITIVE_KEYS = frozenset({
        "api_key",
        "authorization",
        "password",
        "secret",
        "token",
    })

    def __init__(
        self,
        downstream: EventSinkProtocol,
        sensitive_keys: Optional[Iterable[str]] = None,
        replacement: str = "[REDACTED]",
    ):
        self._downstream = downstream
        self._sensitive_keys = {
            key.lower() for key in (sensitive_keys or self.DEFAULT_SENSITIVE_KEYS)
        }
        self._replacement = replacement

    def publish(self, event: HarnessEvent) -> None:
        redacted = event.model_copy(update={"data": self._redact(event.data)})
        self._downstream.publish(redacted)

    def _redact(self, value: Any) -> Any:
        if isinstance(value, dict):
            return {
                key: (
                    self._replacement
                    if str(key).lower() in self._sensitive_keys
                    else self._redact(item)
                )
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [self._redact(item) for item in value]
        return value


class JsonlEventSink:
    """Append each event as one crash-readable JSON line."""

    def __init__(self, path: str):
        self.path = Path(path)
        self._lock = RLock()

    def publish(self, event: HarnessEvent) -> None:
        payload = json.dumps(event.model_dump(mode="json"), ensure_ascii=False)
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(payload + "\n")


class ConsoleEventSink:
    """Small live event surface suitable for examples and debugging."""

    def __init__(self, printer: Callable[[str], None] = print):
        self._printer = printer

    def publish(self, event: HarnessEvent) -> None:
        location = f" step={event.step_id}" if event.step_id is not None else ""
        self._printer(
            f"[{event.sequence:04d}] {event.type.value}{location} run={event.run_id}"
        )
