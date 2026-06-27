"""Thread-safe, bounded event log so the dashboard can stream the thought-chain.

The state machine runs in a worker thread and calls publish(); the SSE endpoint
(async) polls get_since() by ABSOLUTE index. Bounded so a long demo can't grow memory.
"""
from __future__ import annotations

import threading
import time

_lock = threading.Lock()
_events: list[dict] = []
_offset = 0  # number of events dropped off the front (keeps indices absolute)
_MAX = 2000


def publish(event: dict) -> dict:
    global _offset
    event = {"ts": time.time(), **event}
    with _lock:
        _events.append(event)
        if len(_events) > _MAX:
            drop = _MAX // 2
            del _events[:drop]
            _offset += drop
    return event


def get_since(index: int) -> tuple[list[dict], int]:
    """Return (events with absolute index >= `index`, next absolute index)."""
    with _lock:
        start = max(0, index - _offset)
        return _events[start:], _offset + len(_events)
