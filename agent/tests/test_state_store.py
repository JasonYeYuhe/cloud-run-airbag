"""Durable state store contract — the concurrency + lease-recovery + dedup invariants Gemini 3.1
Pro + 3.5 Flash required before trusting the store under the safety-critical compensation logic.
Runs against the memory backend by default (its lock mimics Firestore transaction atomicity) AND
against the REAL google-cloud-firestore transactions in CI's firestore-emulator job
(AIRBAG_TEST_FIRESTORE=1 — see conftest). conftest isolates the store per test in both modes."""
import os
import threading
import time

import pytest

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


# --- v6 Phase 2: transact_multi (write the log head pointer + the immutable entry ATOMICALLY) -------
def test_transact_multi_writes_all_docs_together():
    """The head pointer + the log entry commit as ONE unit — both present, result returned."""
    def _mut(cur):
        assert cur is None
        return ([("log_head", "h", {"seq": 1, "prev_entry_hash": "genesis"}),
                 ("log_entries", "1", {"seq": 1, "incident_id": "inc-a"})], "committed")
    assert state_store.transact_multi("log_head", "h", _mut) == "committed"
    assert state_store.get("log_head", "h") == {"seq": 1, "prev_entry_hash": "genesis"}
    assert state_store.get("log_entries", "1") == {"seq": 1, "incident_id": "inc-a"}


def test_transact_multi_reads_the_current_head_then_advances():
    state_store.put("log_head", "h", {"seq": 5, "prev_entry_hash": "abc"})
    seen = {}

    def _mut(cur):
        seen["prev"] = (cur or {}).get("prev_entry_hash")
        nxt = (cur or {}).get("seq", 0) + 1
        return ([("log_head", "h", {"seq": nxt, "prev_entry_hash": "def"}),
                 ("log_entries", str(nxt), {"seq": nxt})], nxt)
    assert state_store.transact_multi("log_head", "h", _mut) == 6
    assert seen["prev"] == "abc"                              # the mutator saw the CURRENT head
    assert state_store.get("log_head", "h")["seq"] == 6
    assert state_store.get("log_entries", "6")["seq"] == 6


def test_transact_multi_is_all_or_nothing_on_failure():
    """The crash-between-writes guarantee: a failure before commit leaves NEITHER the advanced head NOR
    an orphan entry — so a half-append can never forge a seq gap the auditor would read as suppression.
    (Runs against the REAL Firestore emulator in CI, where @transactional enforces it.)"""
    state_store.put("log_head", "h", {"seq": 5, "prev_entry_hash": "abc"})

    def _boom(cur):
        raise RuntimeError("container killed between head-advance and entry-write")
    with pytest.raises(RuntimeError):
        state_store.transact_multi("log_head", "h", _boom)
    assert state_store.get("log_head", "h") == {"seq": 5, "prev_entry_hash": "abc"}   # head UNCHANGED
    assert state_store.get("log_entries", "6") is None                               # no orphan entry


def test_transact_multi_empty_writes_is_an_idempotent_noop():
    """The KEEP case: `(incident_id, terminal_status)` already logged -> no writes, no dup seq."""
    state_store.put("log_head", "h", {"seq": 5})
    assert state_store.transact_multi("log_head", "h", lambda cur: ([], "kept")) == "kept"
    assert state_store.get("log_head", "h") == {"seq": 5}     # nothing written


def test_transact_multi_rejects_a_malformed_write_before_any_lands():
    """A non-dict doc fails loudly and IDENTICALLY on both backends, with no partial write."""
    state_store.put("log_head", "h", {"seq": 5})
    with pytest.raises((TypeError, ValueError)):
        state_store.transact_multi("log_head", "h",
                                   lambda cur: ([("log_head", "h", {"seq": 6}), ("log_entries", "6", None)], "x"))
    assert state_store.get("log_head", "h") == {"seq": 5}     # first write never landed either


# --- v5 Phase 1.1: per-service correlation lease (storm coalescing) --------------------------------
def test_service_heal_exactly_one_leader():
    """N CONCURRENT alerts (distinct incident ids, ONE service) -> exactly ONE leader; the other N-1
    ATTACH to it (transactional append, no lost ids). The storm's N-for-1 heal fanout collapses to 1.
    Same contract + backend-divergence handling as test_transact_is_atomic_under_contention: on the
    real backend a same-instant WRITE stampede aborts some appenders (their alert is redelivered by
    Cloud Tasks and attaches on the retry); on memory the lock serializes so every alert commits."""
    n = _N_CONTENTION
    barrier = threading.Barrier(n)
    roles: list[tuple] = []
    lock = threading.Lock()

    def worker(i):
        barrier.wait()
        try:
            role = state_store.claim_service_heal("svc", f"inc-{i}", 600)
        except Exception:  # noqa: BLE001 — an optimistic-transaction abort is the caller's to retry
            return
        with lock:
            roles.append(role)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    leaders = [r for r in roles if r[0] == "leader"]
    followers = [r for r in roles if r[0] == "follower"]
    assert len(leaders) == 1, f"expected exactly ONE leader, got {len(leaders)}"
    doc = state_store.get_service_heal("svc")
    leader_id = doc["leader_incident_id"]
    assert all(f[1] == leader_id for f in followers)              # every follower attached to THE leader
    assert len(doc["attached"]) == len(followers)                # no lost ids among the commits
    assert len(set(doc["attached"])) == len(doc["attached"])     # no duplicates
    assert leader_id not in doc["attached"]                      # the leader never attaches to itself
    if config.STATE_BACKEND == "memory":
        assert len(roles) == n and len(followers) == n - 1       # the lock serializes: nobody aborts


def test_service_heal_same_incident_resumes_as_leader():
    """A transient-retry redelivery of the SAME incident resumes as leader — never a follower of itself."""
    assert state_store.claim_service_heal("svc", "inc-1", 600)[0] == "leader"
    assert state_store.claim_service_heal("svc", "inc-1", 600)[0] == "leader"  # resume, not attach
    assert state_store.get_service_heal("svc")["attached"] == []


def test_service_heal_ttl_backstop_takeover():
    """A crashed leader (never settled) is taken over once its lease backstop lapses — no permanent
    lock. (Window matches test_lease_recovers_after_expiry so it doesn't flake on the emulator.)"""
    assert state_store.claim_service_heal("svc", "leader", 0.4)[0] == "leader"
    assert state_store.claim_service_heal("svc", "other", 0.4)[0] == "follower"  # still live -> attach
    time.sleep(0.5)
    assert state_store.claim_service_heal("svc", "taker", 0.4)[0] == "leader"    # backstop lapsed -> takeover
    assert state_store.get_service_heal("svc")["leader_incident_id"] == "taker"


def test_service_heal_holds_while_awaiting_then_releases():
    """A HELD settle keeps the lease live (re-fires coalesce); a RELEASE settle frees it (fresh leader)."""
    assert state_store.claim_service_heal("svc", "leader", 600)[0] == "leader"
    state_store.settle_service_heal("svc", "leader", "awaiting_approval", hold_seconds=600)
    assert state_store.claim_service_heal("svc", "refire", 600) == ("follower", "leader")  # still held
    state_store.settle_service_heal("svc", "leader", "mitigated", hold_seconds=0)
    assert state_store.claim_service_heal("svc", "fresh", 600)[0] == "leader"    # released -> fresh leader
    assert state_store.get_service_heal("svc")["leader_incident_id"] == "fresh"


def test_service_heal_terminal_failed_takeover():
    """STORE PRIMITIVE: a terminally-failed leader (manual_intervention, hold 0) is taken over by the
    next alert. (The run_self_heal-level wiring — the exhausted branch actually CALLING this settle —
    is covered by test_storm_coalesce.test_exhausted_leader_releases_lease_for_takeover.)"""
    assert state_store.claim_service_heal("svc", "leader", 600)[0] == "leader"
    state_store.settle_service_heal("svc", "leader", "manual_intervention", hold_seconds=0)
    assert state_store.claim_service_heal("svc", "taker", 600)[0] == "leader"


def test_settle_no_clobber_after_takeover():
    """A stale settle from a taken-over leader must NOT clobber the new leader's live lease."""
    state_store.claim_service_heal("svc", "old", 0.3)
    time.sleep(0.4)
    state_store.claim_service_heal("svc", "new", 600)                            # takes over
    state_store.settle_service_heal("svc", "old", "mitigated", hold_seconds=0)   # stale -> no-op
    doc = state_store.get_service_heal("svc")
    assert doc["leader_incident_id"] == "new" and doc.get("outcome") is None     # 'new' untouched
    assert state_store.claim_service_heal("svc", "f", 600) == ("follower", "new")  # 'new' still live


def test_settle_returns_attached_ids_for_fanout():
    """settle returns the attached ids — the fan-out set Phase 1.3 settles terminally."""
    state_store.claim_service_heal("svc", "leader", 600)
    state_store.claim_service_heal("svc", "f1", 600)
    state_store.claim_service_heal("svc", "f2", 600)
    assert set(state_store.settle_service_heal("svc", "leader", "mitigated", hold_seconds=0)) == {"f1", "f2"}


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
