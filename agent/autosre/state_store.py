"""Pluggable durable state store (AIRBAG_STATE = memory | firestore), behind one atomic
read-modify-write primitive so pending.py / incidents.py / webhook-dedup keep the same APIs.

  memory   — in-process dict + a global lock (default; fast, local/CI; lost on restart)
  firestore — google-cloud-firestore; survives Cloud Run container recycles + multi-instance

Reviewed by Gemini 3.1 Pro + 3.5 Flash. Key fixes baked in:
  * the completion lock is a LEASE (`completing_lease_until` timestamp), never a sticky boolean —
    a crashed heal can't lock a pending revert forever; an expired lease is re-acquirable.
  * webhook dedup checks `expires_at` LAZILY for correctness (Firestore native TTL is lazy, ≤72h,
    so it's only a storage-cleanup mechanism, not the correctness gate).
  * the memory backend serializes transact() under a lock so it mimics Firestore atomicity (tests
    can't pass on concurrency-unsafe logic).
"""
from __future__ import annotations

import copy
import threading
import time

from . import config

_lock = threading.RLock()
_mem: dict[str, dict[str, dict]] = {}  # collection -> {id -> doc}
_fs_client = None


def _firestore():
    global _fs_client
    if _fs_client is None:
        from google.cloud import firestore
        _fs_client = firestore.Client(project=config.GCP_PROJECT or None)
    return _fs_client


def _use_firestore() -> bool:
    return config.STATE_BACKEND == "firestore"


def transact(collection: str, doc_id: str, mutator):
    """Atomically read doc -> mutator(current_or_None) -> (new_doc | None=delete | KEEP, result);
    write the new doc (or delete) and return `result`. The whole step is atomic per (coll, id)."""
    if _use_firestore():
        return _transact_firestore(collection, doc_id, mutator)
    with _lock:
        cur = _mem.get(collection, {}).get(doc_id)
        # deepcopy at every boundary so a caller mutating a nested field (e.g. events) can never
        # reach back into _mem outside the lock — strict isolation, matching Firestore semantics.
        new, result = mutator(copy.deepcopy(cur) if cur else None)
        if new is KEEP:
            return result
        coll = _mem.setdefault(collection, {})
        if new is None:
            coll.pop(doc_id, None)
        else:
            coll[doc_id] = copy.deepcopy(new)
        return result


def _transact_firestore(collection: str, doc_id: str, mutator):
    from google.cloud import firestore
    client = _firestore()
    ref = client.collection(collection).document(doc_id)

    @firestore.transactional
    def _txn(txn):
        snap = ref.get(transaction=txn)
        cur = snap.to_dict() if snap.exists else None
        new, result = mutator(dict(cur) if cur else None)
        if new is KEEP:
            return result
        if new is None:
            txn.delete(ref)
        else:
            txn.set(ref, dict(new))
        return result

    return _txn(client.transaction())


KEEP = object()  # mutator sentinel: read-only, don't write


def get(collection: str, doc_id: str) -> dict | None:
    if _use_firestore():
        snap = _firestore().collection(collection).document(doc_id).get()
        return snap.to_dict() if snap.exists else None
    with _lock:
        cur = _mem.get(collection, {}).get(doc_id)
        return copy.deepcopy(cur) if cur else None


def put(collection: str, doc_id: str, doc: dict) -> None:
    if _use_firestore():
        _firestore().collection(collection).document(doc_id).set(dict(doc))
        return
    with _lock:
        _mem.setdefault(collection, {})[doc_id] = copy.deepcopy(doc)


def delete(collection: str, doc_id: str) -> None:
    if _use_firestore():
        _firestore().collection(collection).document(doc_id).delete()
        return
    with _lock:
        _mem.get(collection, {}).pop(doc_id, None)


def list_recent(collection: str, n: int, order_field: str = "updated_at") -> list[dict]:
    if _use_firestore():
        from google.cloud import firestore
        q = (_firestore().collection(collection)
             .order_by(order_field, direction=firestore.Query.DESCENDING).limit(n))
        return [d.to_dict() for d in q.stream()]
    with _lock:
        docs = [copy.deepcopy(d) for d in _mem.get(collection, {}).values()]
    docs.sort(key=lambda d: d.get(order_field) or 0, reverse=True)
    return docs[:n]


# --- shared helpers built on transact() ---------------------------------------------
def acquire_lease(collection: str, doc_id: str, field: str, lease_seconds: float) -> dict | None:
    """CAS lease: return the doc (with the lease set) if it exists and the lease is absent/expired;
    else None. Self-healing — a crashed holder's lease expires and the doc becomes re-acquirable."""
    now = time.time()

    def _m(cur):
        if cur is None:
            return KEEP, None                       # nothing to complete
        if cur.get(field, 0) and cur[field] > now:
            return KEEP, None                       # currently leased by someone live
        cur[field] = now + lease_seconds
        return cur, dict(cur)

    return transact(collection, doc_id, _m)


def seen_and_mark(collection: str, doc_id: str, ttl_seconds: float) -> bool:
    """Exactly-once dedup: True if this id was seen and is still within its TTL; else mark it
    (create/refresh expires_at) and return False. Correctness uses expires_at, NOT native TTL."""
    now = time.time()

    def _m(cur):
        if cur and cur.get("expires_at", 0) > now:
            return KEEP, True                       # already seen, still valid
        return {"expires_at": now + ttl_seconds, "marked_at": now}, False

    return transact(collection, doc_id, _m)


def reset_memory() -> None:
    """Test helper — clear the in-memory store."""
    with _lock:
        _mem.clear()
