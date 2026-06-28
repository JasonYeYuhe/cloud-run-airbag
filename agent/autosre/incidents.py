"""In-process incident store: each heal / transaction run is persisted with its full evidence
so the dashboard can render a verifiable "incident report" Artifact — the agent's decision,
the signals it acted on, before/after metrics, the timeline, and the fix PR. This is the
"AI isn't guessing" proof: a judge (or an on-call human) can audit exactly what happened.

Bounded + in-process (paired with --min-instances=1). Durable storage (Firestore) is roadmap.
"""
from __future__ import annotations

import threading
import time

_lock = threading.Lock()
_incidents: dict[str, dict] = {}
_order: list[str] = []
_MAX = 200
_MAX_EVENTS = 500  # cap per-incident timeline (bounds memory + the O(n) de-dup)


def _snapshot(v: dict) -> dict:
    """Isolated copy — the events list is copied so callers (e.g. the report renderer running
    outside the lock) never iterate a list the CI-watcher thread is concurrently extending."""
    snap = dict(v)
    snap["events"] = list(v.get("events", []))
    return snap


def record(incident_id: str, data: dict) -> dict:
    """Create or merge-update an incident record. `events` is appended (de-duplicated by ts)."""
    with _lock:
        cur = _incidents.get(incident_id)
        if cur is None:
            cur = {"incident_id": incident_id, "first_seen": time.time(), "events": []}
            _order.append(incident_id)
            if len(_order) > _MAX:
                _incidents.pop(_order.pop(0), None)
        new_events = data.pop("events", None)
        cur.update(data)
        cur["updated_at"] = time.time()
        if new_events:
            seen = {e.get("ts") for e in cur["events"]}
            cur["events"].extend(e for e in new_events if e.get("ts") not in seen)
            if len(cur["events"]) > _MAX_EVENTS:
                cur["events"] = cur["events"][-_MAX_EVENTS:]
        _incidents[incident_id] = cur
        return _snapshot(cur)


def get(incident_id: str) -> dict | None:
    with _lock:
        v = _incidents.get(incident_id)
        return _snapshot(v) if v else None


def list_recent(n: int = 50) -> list[dict]:
    """Recent incidents (newest first) as compact summaries (no full event list)."""
    with _lock:
        out = []
        for iid in reversed(_order[-n:]):
            v = _incidents.get(iid)
            if not v:
                continue
            out.append({k: v.get(k) for k in (
                "incident_id", "service", "status", "first_seen", "updated_at",
                "rolled_back_to", "restored_to", "pr_url", "error_before", "error_after")}
                | {"stages": len(v.get("events", []))})
        return out
