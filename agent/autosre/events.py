"""Thread-safe, bounded event log so the dashboard can stream the thought-chain.

The state machine runs in a worker thread and calls publish(); the SSE endpoint (async) polls
get_since() by ABSOLUTE index. Bounded so a long demo can't grow memory.

Multi-instance (AIRBAG_EVENTS=pubsub): each instance keeps its own local log, and publish() ALSO
fans the event out over a Pub/Sub topic. Every instance runs a subscriber that mirrors OTHER
instances' events into its local log — so a dashboard SSE connected to instance A sees a heal that
ran on instance B. This keeps get_since()/the SSE contract UNCHANGED; the local log just becomes a
per-instance mirror of the global stream. Default (inproc) is the single-instance in-process bus.
"""
from __future__ import annotations

import json
import logging
import threading
import time
import uuid

from . import config

log = logging.getLogger("airbag.events")

_lock = threading.Lock()
_events: list[dict] = []
_offset = 0  # number of events dropped off the front (keeps indices absolute)
_MAX = 2000
_SELF = uuid.uuid4().hex[:12]   # this instance's id — to skip our own fanned-out messages
_seen: set[str] = set()         # dedup by event id (bounded)
_publisher = None
_topic_path = None


def _append_local(event: dict) -> None:
    global _offset
    with _lock:
        eid = event.get("_eid")
        if eid:
            if eid in _seen:
                return                       # already have it (our own, or a redelivery)
            _seen.add(eid)
            if len(_seen) > _MAX:
                _seen.clear()                # crude bound (a rare late dup is harmless)
        _events.append(event)
        if len(_events) > _MAX:
            drop = _MAX // 2
            del _events[:drop]
            _offset += drop


def publish(event: dict) -> dict:
    event = {"ts": time.time(), "_eid": uuid.uuid4().hex, "_src": _SELF, **event}
    _append_local(event)                     # instant local delivery (unchanged behavior)
    if config.EVENTS_BACKEND == "pubsub":
        try:
            pub, topic = _pubsub()
            pub.publish(topic, json.dumps(event).encode("utf-8"))
        except Exception as e:               # never let the event bus break a heal
            log.warning("pubsub publish failed: %s", e)
    return event


def get_since(index: int) -> tuple[list[dict], int]:
    """Return (events with absolute index >= `index`, next absolute index)."""
    with _lock:
        start = max(0, index - _offset)
        return _events[start:], _offset + len(_events)


def _pubsub():
    global _publisher, _topic_path
    if _publisher is None:
        from google.cloud import pubsub_v1
        _publisher = pubsub_v1.PublisherClient()
        _topic_path = _publisher.topic_path(config.GCP_PROJECT, config.EVENTS_TOPIC)
    return _publisher, _topic_path


def start_subscriber() -> None:
    """In pubsub mode, mirror OTHER instances' events into the local log (cross-instance fan-out).
    Idempotent + best-effort: a Pub/Sub failure must never take down the agent."""
    if config.EVENTS_BACKEND != "pubsub":
        return

    def _run():
        try:
            from google.cloud import pubsub_v1
            sub = pubsub_v1.SubscriberClient()
            topic = sub.topic_path(config.GCP_PROJECT, config.EVENTS_TOPIC)
            sub_path = sub.subscription_path(config.GCP_PROJECT, f"airbag-events-{_SELF}")
            try:  # per-instance subscription, auto-expiring so recycled instances clean themselves up
                sub.create_subscription(request={
                    "name": sub_path, "topic": topic,
                    "expiration_policy": {"ttl": {"seconds": 86400}},
                    "message_retention_duration": {"seconds": 600}})
            except Exception:  # noqa: BLE001 — already exists / race
                pass

            def _cb(message):
                try:
                    ev = json.loads(message.data.decode("utf-8"))
                    if ev.get("_src") != _SELF:          # our own already delivered locally
                        _append_local(ev)
                except Exception:  # noqa: BLE001
                    pass
                message.ack()

            future = sub.subscribe(sub_path, callback=_cb)
            log.info("events: pubsub subscriber up (instance %s)", _SELF)
            future.result()                              # block this daemon thread on the stream
        except Exception as e:  # noqa: BLE001
            log.warning("events: pubsub subscriber failed (%s) — local-only on this instance", e)

    threading.Thread(target=_run, daemon=True).start()
