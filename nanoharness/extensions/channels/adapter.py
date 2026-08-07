"""Channel adapter protocol and deterministic in-memory adapter."""

from __future__ import annotations

import hashlib
import re
import threading
from typing import Dict, Protocol, runtime_checkable

from nanoharness.extensions.channels.models import (
    DeliveryReceipt,
    OutboundEnvelope,
)


class ChannelDeliveryError(RuntimeError):
    pass


@runtime_checkable
class ChannelAdapterProtocol(Protocol):
    channel: str

    def send(
        self,
        envelope: OutboundEnvelope,
        *,
        idempotency_key: str,
    ) -> DeliveryReceipt:
        ...

    def close(self) -> None:
        ...


class MockChannelAdapter:
    """Network-free adapter with observable, idempotent deliveries."""

    def __init__(self, channel: str = "mock", *, failures_before_success: int = 0):
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", channel):
            raise ValueError("channel name is invalid")
        if failures_before_success < 0:
            raise ValueError("failures_before_success must be non-negative")
        self.channel = channel
        self.failures_before_success = failures_before_success
        self.deliveries: list[DeliveryReceipt] = []
        self._receipts: Dict[str, DeliveryReceipt] = {}
        self._attempts: Dict[str, int] = {}
        self._lock = threading.RLock()
        self._closed = False

    @property
    def closed(self) -> bool:
        with self._lock:
            return self._closed

    def attempt_count(self, idempotency_key: str) -> int:
        with self._lock:
            return self._attempts.get(idempotency_key, 0)

    def send(
        self,
        envelope: OutboundEnvelope,
        *,
        idempotency_key: str,
    ) -> DeliveryReceipt:
        with self._lock:
            if self._closed:
                raise ChannelDeliveryError("channel adapter is closed")
            if envelope.channel != self.channel:
                raise ChannelDeliveryError(
                    f"adapter {self.channel!r} cannot deliver channel "
                    f"{envelope.channel!r}"
                )
            existing = self._receipts.get(idempotency_key)
            if existing is not None:
                return existing.model_copy(deep=True)

            attempt = self._attempts.get(idempotency_key, 0) + 1
            self._attempts[idempotency_key] = attempt
            if attempt <= self.failures_before_success:
                raise ChannelDeliveryError(
                    f"mock delivery failed on attempt {attempt}"
                )

            digest = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()[:24]
            receipt = DeliveryReceipt(
                channel=self.channel,
                idempotency_key=idempotency_key,
                external_delivery_id=f"mock_{digest}",
                metadata={"attempt": attempt},
            )
            self._receipts[idempotency_key] = receipt
            self.deliveries.append(receipt.model_copy(deep=True))
            return receipt.model_copy(deep=True)

    def close(self) -> None:
        with self._lock:
            self._closed = True
