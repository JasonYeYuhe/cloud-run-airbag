"""'Pending revert' state: after a rollback, what's needed to later undo it + close the
transaction. Backed by the pluggable durable store (state_store) — in-memory by default,
Firestore when AIRBAG_STATE=firestore (survives Cloud Run recycles + multi-instance).

The completion lock is a LEASE (`completing_lease_until`), not a sticky boolean: a crashed
complete_rollback can't lock a pending revert forever — the lease expires and it's re-acquirable.
"""
from __future__ import annotations

import time

from . import config, state_store

_COLL = "pending"


def set_pending(service: str, data: dict) -> None:
    # defaults BEFORE **data so the caller can override; Firestore order_by(rollback_at_epoch) in
    # all_pending() SILENTLY omits docs missing that field, so it must always be present.
    record = {"rollback_at_epoch": 0, "service": service, "attempts": 0, **data}
    record["completing_lease_until"] = 0  # a freshly-set pending starts unlocked (no completion yet)
    state_store.put(_COLL, service, record)


def get_pending(service: str) -> dict | None:
    return state_store.get(_COLL, service)


def try_begin_complete(service: str) -> dict | None:
    """Atomically claim the right to run complete_rollback (lease-based). Returns the pending
    record if claimed, or None if there's nothing pending OR a completion is already in flight
    (an unexpired lease). Idempotent: a duplicate /internal/complete-rollback is a no-op."""
    return state_store.acquire_lease(_COLL, service, "completing_lease_until",
                                     config.COMPLETE_LEASE_S)


def bump_attempts(service: str) -> int:
    def _m(cur):
        if not cur:
            return state_store.KEEP, 0
        cur["attempts"] = cur.get("attempts", 0) + 1
        return cur, cur["attempts"]
    return state_store.transact(_COLL, service, _m)


def end_complete(service: str, *, closed: bool) -> None:
    """Drop the record if the transaction closed; else release the lease so a later retry (after
    a real fix deploys) can re-acquire it."""
    if closed:
        state_store.delete(_COLL, service)
        return

    def _m(cur):
        if not cur:
            return state_store.KEEP, None
        cur["completing_lease_until"] = 0
        return cur, None
    state_store.transact(_COLL, service, _m)


def clear_pending(service: str) -> None:
    state_store.delete(_COLL, service)


def all_pending() -> dict[str, dict]:
    return {d.get("service", "?"): d for d in state_store.list_recent(_COLL, 1000, "rollback_at_epoch")}


def _now() -> float:
    return time.time()
