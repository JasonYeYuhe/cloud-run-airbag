"""Durable state store contract — the concurrency + lease-recovery + dedup invariants Gemini 3.1
Pro + 3.5 Flash required before trusting the store under the safety-critical compensation logic.
Run against the memory backend (its lock mimics Firestore transaction atomicity); the same suite
should run against the Firestore emulator in CI (follow-up). conftest pins STATE_BACKEND=memory +
resets the store per test."""
import threading
import time

from autosre import config, pending, state_store


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
    """A crashed holder's lease expires -> the pending becomes re-acquirable (no permanent lock)."""
    monkeypatch.setattr(config, "COMPLETE_LEASE_S", 0.05)
    pending.set_pending("svc", {"incident_id": "i1"})
    assert pending.try_begin_complete("svc") is not None   # first acquires
    assert pending.try_begin_complete("svc") is None        # still leased
    time.sleep(0.08)
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
    assert state_store.seen_and_mark("dedup", "id1", 0.05) is False
    time.sleep(0.08)
    assert state_store.seen_and_mark("dedup", "id1", 0.05) is False  # expired -> treated as unseen


def test_transact_is_atomic_under_contention():
    """100 concurrent increments through transact() -> no lost updates."""
    barrier = threading.Barrier(100)

    def inc():
        def _m(cur):
            cur = cur or {"n": 0}
            cur["n"] += 1
            return cur, cur["n"]
        barrier.wait()
        state_store.transact("counters", "x", _m)

    threads = [threading.Thread(target=inc) for _ in range(100)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert state_store.get("counters", "x")["n"] == 100


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
