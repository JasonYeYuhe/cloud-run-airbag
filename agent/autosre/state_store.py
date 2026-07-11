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


def transact_multi(collection: str, doc_id: str, mutator):
    """Atomically read ONE doc (collection/doc_id — the transparency `log_head`) -> mutator(current) ->
    (writes, result), where writes = [(collection, doc_id, new_doc), ...] are ALL applied together
    (all-or-nothing) and `result` is returned. This is Phase 2's first primitive: single-doc transact()
    cannot write the head pointer AND the immutable log entry atomically, so a container kill between
    the head-advance and the entry-write would leave a permanent seq gap the auditor would read as
    SUPPRESSION (a false tamper alarm). Both backends guarantee all-or-nothing: Firestore via one
    @transactional commit, memory via the _lock + stage-then-apply. An empty writes list is a no-op
    (the idempotent KEEP case — `(incident_id, terminal_status)` already logged). Read-before-write
    order holds: only `log_head` is read, and every write follows."""
    if _use_firestore():
        return _transact_multi_firestore(collection, doc_id, mutator)
    with _lock:
        cur = _mem.get(collection, {}).get(doc_id)
        writes, result = mutator(copy.deepcopy(cur) if cur else None)
        # stage (validate + deepcopy) every write FIRST so a malformed write / copy error aborts before
        # ANY write lands; the apply loop is then pure dict assignment -> all-or-nothing like the txn.
        staged = _staged_writes(writes)
        for c, i, d in staged:
            _mem.setdefault(c, {})[i] = d
        return result


def _staged_writes(writes) -> list[tuple[str, str, dict]]:
    """Validate + deep-materialize a mutator's write list so misuse fails LOUDLY and IDENTICALLY on both
    backends (transact_multi has no delete semantics — every write is a (collection, doc_id, dict))."""
    staged: list[tuple[str, str, dict]] = []
    for w in writes:
        coll, did, doc = w                                    # a malformed tuple raises here, pre-write
        if not isinstance(doc, dict):
            raise TypeError(f"transact_multi write for {coll}/{did} must be a dict, got {type(doc).__name__}")
        staged.append((coll, did, copy.deepcopy(doc)))
    return staged


def _transact_multi_firestore(collection: str, doc_id: str, mutator):
    from google.cloud import firestore
    client = _firestore()
    ref = client.collection(collection).document(doc_id)

    @firestore.transactional
    def _txn(txn):
        snap = ref.get(transaction=txn)                       # the ONLY read (read-before-write holds)
        cur = snap.to_dict() if snap.exists else None
        writes, result = mutator(dict(cur) if cur else None)
        for c, i, d in _staged_writes(writes):                # validate before buffering any write
            txn.set(client.collection(c).document(i), d)      # all writes commit together, or none
        return result

    return _txn(client.transaction())


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


def claim_heal(doc_id: str, lease_seconds: float, ttl_seconds: float, max_attempts: int) -> str:
    """Per-incident idempotency guard AND circuit breaker for run_self_heal. Returns:
      "claimed"   — THIS caller may run the heal (and the attempt count was bumped),
      "duplicate" — already running (unexpired lease) or already done -> drop the redelivery,
      "exhausted" — failed max_attempts times -> give up (marked done so it stops redelivering).
    Claim BEFORE any side effect. The attempt count bounds Cloud Tasks at-least-once retries so a
    DETERMINISTICALLY-failing heal can't loop forever re-running side effects."""
    now = time.time()

    def _m(cur):
        if cur:
            if cur.get("done"):
                return KEEP, "duplicate"
            if cur.get("running_lease_until", 0) > now:
                return KEEP, "duplicate"
        attempts = (cur or {}).get("attempts", 0)
        if attempts >= max_attempts:                     # circuit breaker tripped -> terminal
            doc = dict(cur or {})
            doc.update({"done": True, "running_lease_until": 0, "exhausted": True,
                        "expires_at": now + ttl_seconds})
            return doc, "exhausted"
        doc = dict(cur or {})
        doc.update({"running_lease_until": now + lease_seconds, "done": False,
                    "attempts": attempts + 1, "claimed_at": now, "expires_at": now + ttl_seconds})
        return doc, "claimed"
    return transact("heal_runs", doc_id, _m)


def finish_heal(doc_id: str, ttl_seconds: float) -> None:
    """Mark a heal done so a late redelivery (after the lease expired) is a no-op (TTL-reclaimed)."""
    now = time.time()

    def _m(cur):
        cur = cur or {}
        cur.update({"done": True, "running_lease_until": 0, "done_at": now,
                    "expires_at": now + ttl_seconds})
        return cur, True
    transact("heal_runs", doc_id, _m)


def release_heal(doc_id: str) -> None:
    """Release the lease WITHOUT marking done (the run failed transiently) so a retry can re-claim."""
    def _m(cur):
        if not cur:
            return KEEP, None
        cur["running_lease_until"] = 0
        return cur, None
    transact("heal_runs", doc_id, _m)


# --- per-service correlation lease (v5 Phase 1.1): coalesce an alert STORM into one heal ----------
_SERVICE_HEALS = "service_heals"


def _service_heal_live(cur: dict | None, now: float) -> bool:
    """Is the leader in `cur` still holding the correlation lease? The lease clock (`lease_until`) is
    the SINGLE source of truth: a fresh claim / running run sets it to now+backstop; a settle re-aims
    it to now+hold (a generous hold while a held state's approval/pending is live, or 0 to release on
    a settled/terminal outcome). So an expired clock ALWAYS means 'take over' — a crashed leader
    (backstop lapsed), a settled outage, or a terminally-failed leader. Purely time-based: no coupling
    to autonomy/pending state (the settle caller, which knows those, sets the hold)."""
    return bool(cur) and cur.get("lease_until", 0) > now


def claim_service_heal(service: str, incident_id: str,
                       lease_seconds: float) -> tuple[str, str | None]:
    """Per-SERVICE correlation lease (keyed on service, NOT the Monitoring incident id). Returns:
      ("leader", None)              — THIS incident runs the heal: a fresh outage, a crashed- or
                                      terminally-failed-leader takeover, OR the same incident resuming
                                      its own run after a transient retry,
      ("follower", leader_incident) — a live leader is already healing this service; THIS incident was
                                      transactionally ATTACHED to it (no lost ids) and should return
                                      before triage (emit no self-amplifying probes).
    Mirrors pending.try_begin_complete — the codebase's proven per-service lease — with an attach path
    added. Liveness is purely the lease clock (see _service_heal_live). Atomic per (collection, service)."""
    now = time.time()

    def _m(cur):
        if _service_heal_live(cur, now):
            if cur.get("leader_incident_id") == incident_id:
                cur["lease_until"] = now + lease_seconds     # same incident resuming -> refresh backstop
                cur["updated_at"] = now
                return cur, ("leader", None)
            att = list(cur.get("attached", []))
            if incident_id not in att:
                att.append(incident_id)                       # transactional append -> no lost ids
            cur["attached"] = att
            cur["updated_at"] = now
            return cur, ("follower", cur.get("leader_incident_id"))
        # fresh outage / crashed-leader takeover / terminally-failed-leader takeover
        doc = {"service": service, "leader_incident_id": incident_id,
               "lease_until": now + lease_seconds, "outcome": None, "attached": [],
               "created_at": (cur or {}).get("created_at", now), "updated_at": now}
        return doc, ("leader", None)

    return transact(_SERVICE_HEALS, service, _m)


def settle_service_heal(service: str, incident_id: str, outcome: str,
                        hold_seconds: float) -> list[str]:
    """Record the leader's settled outcome and re-aim the lease clock. `hold_seconds` > 0 keeps the
    lease live (a HELD state — a live approval/pending that late re-fires must still coalesce onto);
    0 releases it (a settled/terminal outcome — the next alert becomes a fresh leader). NO-OP unless
    `incident_id` is STILL the current leader, so a stale settle from a taken-over leader can never
    clobber the new one. Returns the attached incident ids (for terminal fan-out settlement in 1.3)."""
    now = time.time()

    def _m(cur):
        if not cur or cur.get("leader_incident_id") != incident_id:
            return KEEP, []
        cur["outcome"] = outcome
        cur["lease_until"] = now + hold_seconds
        cur["settled_at"] = now
        cur["updated_at"] = now
        return cur, list(cur.get("attached", []))

    return transact(_SERVICE_HEALS, service, _m)


def get_service_heal(service: str) -> dict | None:
    return get(_SERVICE_HEALS, service)


def reset_memory() -> None:
    """Test helper — clear the in-memory store."""
    with _lock:
        _mem.clear()
