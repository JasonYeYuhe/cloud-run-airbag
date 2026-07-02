"""Durable state store contract — the concurrency + lease-recovery + dedup invariants Gemini 3.1
Pro + 3.5 Flash required before trusting the store under the safety-critical compensation logic.
Runs against the memory backend by default (its lock mimics Firestore transaction atomicity) AND
against the REAL google-cloud-firestore transactions in CI's firestore-emulator job
(AIRBAG_TEST_FIRESTORE=1 — see conftest). conftest isolates the store per test in both modes."""
import os
import threading
import time

from autosre import config, pending, state_store

# The memory backend's lock serializes writers (all succeed); real Firestore transactions are
# OPTIMISTIC with bounded retries (client max 5), so a deliberate same-instant stampede on one doc
# ABORTS some writers loudly ("409 Transaction lock timeout" — observed on the emulator in CI).
# That is designed behavior at the callers (queue redelivery / lease release + retry); the store's
# CONTRACT is atomicity — no LOST updates among the transactions that committed.
_N_CONTENTION = 12 if os.environ.get("AIRBAG_TEST_FIRESTORE") else 100


def test_acquire_lease_exactly_one_winner():
    """10 concurrent try_begin_complete on the same pending -> exactly one claims it."""
    pending.set_pending("svc", {"incident_id": "i1", "bad_revision": "b"})
    wins = []
    barrier = threading.Barrier(10)

    def worker():
        barrier.wait()
        r = pending.try_begin_complete("svc")
        if r is not None:
            wins.append(r)

    threads = [threading.Thread(target=worker) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(wins) == 1


def test_lease_recovers_after_expiry(monkeypatch):
    """A crashed holder's lease expires -> the pending becomes re-acquirable (no permanent lock).
    (Window sized for the emulator run: a real-backend round-trip takes ms, so a 50ms lease could
    expire between the acquire and the still-leased check and flake.)"""
    monkeypatch.setattr(config, "COMPLETE_LEASE_S", 0.4)
    pending.set_pending("svc", {"incident_id": "i1"})
    assert pending.try_begin_complete("svc") is not None   # first acquires
    assert pending.try_begin_complete("svc") is None        # still leased
    time.sleep(0.5)
    assert pending.try_begin_complete("svc") is not None     # lease expired -> recoverable


def test_no_pending_is_noop():
    assert pending.try_begin_complete("absent") is None


def test_end_complete_closed_deletes_open_releases():
    pending.set_pending("svc", {"incident_id": "i1"})
    assert pending.try_begin_complete("svc") is not None
    pending.end_complete("svc", closed=False)               # release the lease (retry later)
    assert pending.try_begin_complete("svc") is not None     # re-acquirable
    pending.end_complete("svc", closed=True)                # transaction closed -> gone
    assert pending.get_pending("svc") is None


def test_dedup_marks_then_detects_duplicate():
    assert state_store.seen_and_mark("dedup", "id1", 100) is False  # first time
    assert state_store.seen_and_mark("dedup", "id1", 100) is True   # duplicate within TTL


def test_dedup_expires_by_expires_at(monkeypatch):
    assert state_store.seen_and_mark("dedup", "id1", 0.3) is False
    time.sleep(0.4)
    assert state_store.seen_and_mark("dedup", "id1", 0.3) is False  # expired -> treated as unseen


def test_transact_is_atomic_under_contention():
    """N same-instant increments through transact() -> NO LOST UPDATES: the final count equals the
    number of commits that reported success. Writers that ABORT (optimistic-transaction stampede on
    the real backend) raise loudly and count for nothing — losing a SUCCESSFUL write is the bug this
    guards. On the memory backend the lock serializes, so all N must additionally succeed."""
    barrier = threading.Barrier(_N_CONTENTION)
    committed: list[int] = []

    def inc():
        def _m(cur):
            cur = cur or {"n": 0}
            cur["n"] += 1
            return cur, cur["n"]
        barrier.wait()
        try:
            state_store.transact("counters", "x", _m)
            committed.append(1)
        except Exception:  # noqa: BLE001 — an abort surfaces to the caller; retries are its job
            pass

    threads = [threading.Thread(target=inc) for _ in range(_N_CONTENTION)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert committed, "every writer aborted — the store made no progress under contention"
    assert state_store.get("counters", "x")["n"] == len(committed)
    if config.STATE_BACKEND == "memory":
        assert len(committed) == _N_CONTENTION   # the lock serializes: nobody may abort


def test_bump_attempts_increments():
    pending.set_pending("svc", {"incident_id": "i1"})
    assert pending.bump_attempts("svc") == 1
    assert pending.bump_attempts("svc") == 2
    assert pending.get_pending("svc")["attempts"] == 2


def test_claim_heal_idempotency():
    assert state_store.claim_heal("inc1", 600, 3600, 5) == "claimed"    # first claim wins
    assert state_store.claim_heal("inc1", 600, 3600, 5) == "duplicate"  # already running (leased)
    state_store.finish_heal("inc1", 3600)
    assert state_store.claim_heal("inc1", 600, 3600, 5) == "duplicate"  # done -> drop redelivery


def test_claim_heal_release_allows_retry():
    assert state_store.claim_heal("inc2", 600, 3600, 5) == "claimed"
    state_store.release_heal("inc2")                            # transient failure (not done)
    assert state_store.claim_heal("inc2", 600, 3600, 5) == "claimed"    # re-claimable for a retry


def test_claim_heal_circuit_breaker_after_max_attempts():
    for _ in range(3):                                          # 3 failed attempts (claim then release)
        assert state_store.claim_heal("inc3", 600, 3600, 3) == "claimed"
        state_store.release_heal("inc3")
    assert state_store.claim_heal("inc3", 600, 3600, 3) == "exhausted"  # cap hit -> give up
    assert state_store.claim_heal("inc3", 600, 3600, 3) == "duplicate"  # marked done -> stop redelivering


def test_set_pending_preserves_inflight_completion_lease():
    import time
    pending.set_pending("svc", {"incident_id": "i1"})
    rec = pending.try_begin_complete("svc")                     # take the completion lease
    assert rec is not None and rec["completing_lease_until"] > time.time()
    pending.bump_attempts("svc")
    pending.set_pending("svc", {"incident_id": "i2"})           # a concurrent re-arm must not stomp it
    after = pending.get_pending("svc")
    assert after["completing_lease_until"] > time.time() and after["attempts"] == 1


# --- ordered reads (v4 Phase 4.1 — the confirmed Firestore gap) -----------------------------------
def test_list_recent_orders_newest_first():
    """The ordered read the incident dashboard + pending/approval sweeps depend on — proven on the
    REAL backend in CI's firestore-emulator job, not just the memory mimic."""
    for doc_id, ts in (("a", 100.0), ("b", 300.0), ("c", 200.0)):
        state_store.put("lr", doc_id, {"updated_at": ts})
    got = state_store.list_recent("lr", 2)
    assert [d["updated_at"] for d in got] == [300.0, 200.0]


def test_list_recent_missing_order_field_divergence():
    """PINNED backend divergence (the hazard behind the always-write-the-order-field rule):
    Firestore's order_by SILENTLY OMITS documents missing the order field; the memory backend
    sorts them as 0 and includes them. Every writer therefore always stamps its order field
    (test below) — this test keeps the divergence itself visible instead of folklore."""
    state_store.put("lr2", "with", {"updated_at": 100.0})
    state_store.put("lr2", "without", {"other": 1})
    got = state_store.list_recent("lr2", 10)
    if config.STATE_BACKEND == "firestore":
        assert [d.get("updated_at") for d in got] == [100.0]     # the unstamped doc vanished
    else:
        assert len(got) == 2                                     # memory includes it (sorted as 0)


def test_every_writer_stamps_its_order_field():
    """The invariant that makes the divergence moot: every collection consumed via list_recent is
    ALWAYS written with its order field present — a new write path that forgets would make docs
    silently vanish from Firestore reads (dashboard incidents, pending sweeps, approvals)."""
    from autosre import autonomy, incidents, memory
    incidents.record("i-ord", {"service": "svc"})               # incidents ordered by updated_at
    assert "updated_at" in state_store.get("incidents", "i-ord")
    autonomy.save_approval("i-appr", {"service": "svc"})        # approvals ordered by created_at
    assert "created_at" in state_store.get("approvals", "i-appr")
    pending.set_pending("svc-ord", {"incident_id": "i1"})       # pending ordered by rollback_at_epoch
    assert "rollback_at_epoch" in state_store.get("pending", "svc-ord")
    memory.witness_serving("svc-ord", "rev-1")                  # service_memory carries updated_at
    assert "updated_at" in state_store.get("service_memory", "svc-ord")
