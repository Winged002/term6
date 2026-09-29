from __future__ import annotations

import inspect
import threading
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable


@dataclass(slots=True)
class Event:
    type: str
    data: dict[str, Any] = field(default_factory=dict)
    ts: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    seq: int = 0


class EventBus:
    """Small in-process event bus with a bounded observable history.

    Observers are best-effort and may never break the runtime. 5.2-alpha2 keeps
    a sequence-numbered ring buffer so the loopback web UI can poll live
    execution state without exposing provider chain-of-thought.
    """

    def __init__(self, history_limit: int = 1200) -> None:
        self._listeners: dict[str, list[Callable[[Event], Any]]] = defaultdict(list)
        self._history: deque[Event] = deque(maxlen=max(100, int(history_limit)))
        self._seq = 0
        self._lock = threading.RLock()

    def subscribe(self, event_type: str, callback: Callable[[Event], Any]) -> None:
        self._listeners[event_type].append(callback)

    async def emit(self, event_type: str, **data: Any) -> Event:
        with self._lock:
            self._seq += 1
            event = Event(event_type, data, seq=self._seq)
            self._history.append(event)
        callbacks = [*self._listeners.get(event_type, []), *self._listeners.get("*", [])]
        for cb in callbacks:
            try:
                result = cb(event)
                if inspect.isawaitable(result):
                    await result
            except Exception:
                # Observers must never break the runtime.
                pass
        return event

    def snapshot(self, *, after: int = 0, limit: int = 250) -> list[Event]:
        after = max(0, int(after))
        limit = min(500, max(1, int(limit)))
        with self._lock:
            rows = [event for event in self._history if event.seq > after]
        return rows[-limit:]

    @property
    def last_seq(self) -> int:
        with self._lock:
            return self._seq
