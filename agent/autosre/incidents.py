"""Incident store: each heal / transaction run is persisted with its full evidence so the
dashboard can render a verifiable "incident report" Artifact — the decision, the signals, the
before/after metrics, the timeline, the fix PR. The "AI isn't guessing" proof: a judge or an
on-call human can audit exactly what happened.

Backed by the pluggable durable store (state_store) — in-memory by default, Firestore when
AIRBAG_STATE=firestore. The per-incident timeline is capped (bounds the Firestore 1 MB doc).
"""
from __future__ import annotations

import time

from . import state_store

_COLL = "incidents"
_MAX_EVENTS = 500  # cap per-incident timeline (bounds doc size + the O(n) de-dup)


def _snapshot(v: dict) -> dict:
    snap = dict(v)
    snap["events"] = list(v.get("events", []))
    return snap


def record(incident_id: str, data: dict) -> dict:
    """Create or merge-update an incident record. `events` is appended (de-duplicated by ts).
    The mutator is pure + idempotent so a Firestore transaction retry is side-effect-free."""
    new_events = data.get("events") or []
    fields = {k: v for k, v in data.items() if k != "events"}

    def _m(cur):
        # evaluated INSIDE the mutator (re-run on a Firestore transaction retry) so updated_at is
        # monotonic — never stale-jumps backward below first_seen — and equals first_seen on create.
        now = time.time()
        if cur is None:
            cur = {"incident_id": incident_id, "first_seen": now, "events": []}
        cur.update(fields)
        cur["updated_at"] = now
        if new_events:
            seen = {e.get("ts") for e in cur.get("events", [])}
            cur["events"] = cur.get("events", []) + [e for e in new_events if e.get("ts") not in seen]
            if len(cur["events"]) > _MAX_EVENTS:
                cur["events"] = cur["events"][-_MAX_EVENTS:]
        return cur, _snapshot(cur)

    return state_store.transact(_COLL, incident_id, _m)


def get(incident_id: str) -> dict | None:
    v = state_store.get(_COLL, incident_id)
    return _snapshot(v) if v else None


def list_recent(n: int = 50) -> list[dict]:
    """Recent incidents (newest first) as compact summaries (no full event list)."""
    out = []
    for v in state_store.list_recent(_COLL, n, "updated_at"):
        out.append({k: v.get(k) for k in (
            "incident_id", "service", "status", "first_seen", "updated_at",
            "rolled_back_to", "restored_to", "pr_url", "error_before", "error_after")}
            | {"stages": len(v.get("events", []))})
    return out
