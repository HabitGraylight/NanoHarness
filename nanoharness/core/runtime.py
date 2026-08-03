"""Thread-safe cooperative controls for active NanoHarness runs."""

from queue import Empty, SimpleQueue
from threading import Event, Lock
from typing import List


class RunControl:
    """Cancellation and steering mailbox shared with a running engine.

    Cancellation is cooperative and is observed at step boundaries. Steering
    messages are appended to conversation context before the next model turn.
    """

    def __init__(self):
        self._cancelled = Event()
        self._reason = ""
        self._reason_lock = Lock()
        self._steering: SimpleQueue[str] = SimpleQueue()

    def cancel(self, reason: str = "Cancelled by caller") -> None:
        with self._reason_lock:
            if not self._cancelled.is_set():
                self._reason = reason
                self._cancelled.set()

    @property
    def cancelled(self) -> bool:
        return self._cancelled.is_set()

    @property
    def cancel_reason(self) -> str:
        with self._reason_lock:
            return self._reason or "Cancelled by caller"

    def steer(self, message: str) -> None:
        if not message or not message.strip():
            raise ValueError("Steering message must not be empty")
        self._steering.put(message)

    def drain_steering(self) -> List[str]:
        messages = []
        while True:
            try:
                messages.append(self._steering.get_nowait())
            except Empty:
                return messages
