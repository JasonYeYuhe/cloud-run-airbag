"""Thread-safe event log so the dashboard can stream the agent's thought-chain.

The state machine runs in a worker thread and calls publish(); the SSE endpoint
(async) polls get_since() by index. No cross-thread asyncio — simple and robust.
"""
from __future__ import annotations

import threading
import time

_lock = threading.Lock()
_events: list[dict] = []


def publish(event: dict) -> dict:
    event = {"ts": time.time(), **event}
    with _lock:
        _events.append(event)
    return event


def get_since(index: int) -> tuple[list[dict], int]:
    with _lock:
        return _events[index:], len(_events)


def total() -> int:
    with _lock:
        return len(_events)
