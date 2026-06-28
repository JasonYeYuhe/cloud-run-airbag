"""In-memory 'pending revert' store: after a rollback, we remember enough to later undo it
(restore traffic to the fixed revision) and close the transaction.

Deliberately in-process — paired with Cloud Run `--min-instances=1` it survives across requests
for the hackathon. Durable state (Firestore) is roadmap; see docs/NEXT_STEPS.md.
"""
from __future__ import annotations

import threading

_lock = threading.Lock()
_pending: dict[str, dict] = {}  # service -> {incident_id, bad_revision, rolled_back_to, rollback_at_iso, pr_url}


def set_pending(service: str, data: dict) -> None:
    with _lock:
        _pending[service] = dict(data)


def get_pending(service: str) -> dict | None:
    with _lock:
        v = _pending.get(service)
        return dict(v) if v else None


def try_begin_complete(service: str) -> dict | None:
    """Atomically claim the right to run complete_rollback for this service. Returns the
    pending record if claimed, or None if there's nothing pending OR a completion is already
    in flight (idempotency: a duplicate /internal/complete-rollback call is a no-op)."""
    with _lock:
        v = _pending.get(service)
        if not v or v.get("_completing"):
            return None
        v["_completing"] = True
        return dict(v)


def end_complete(service: str, *, closed: bool) -> None:
    """Finish a completion: drop the record if the transaction closed, else release the
    in-flight flag so a later retry (after a real fix deploys) can run."""
    with _lock:
        if closed:
            _pending.pop(service, None)
        elif service in _pending:
            _pending[service].pop("_completing", None)


def clear_pending(service: str) -> None:
    with _lock:
        _pending.pop(service, None)


def all_pending() -> dict[str, dict]:
    with _lock:
        return {k: dict(v) for k, v in _pending.items()}
