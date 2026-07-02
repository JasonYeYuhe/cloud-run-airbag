"""v5 Phase 1.1 — storm coalescing at the state-machine seam.

A follower ATTACHES (no second heal, no self-amplifying triage probes) and the leader's settle
re-aims the per-service correlation lease to match the outcome (release on mitigated/denied, HOLD
while a live approval must coalesce late re-fires). Deterministic — no threads here: the CONCURRENT
transactional safety (N simultaneous alerts -> one leader) is proven by the threaded lease-contention
suite in test_state_store.py. Mock backend; conftest pins the memory store + resets it per test.
"""
import time

import pytest

from autosre import autonomy, config, incidents, pending, state_store
from autosre.backends import mock
from autosre.state_machine import apply_approval, complete_rollback, run_self_heal

SVC = "airbag-target"


def setup_function(_):
    mock.reset()  # bad revision serving -> a ROLLBACK decision
    config.GEMINI_API_KEY = ""


@pytest.fixture
def storm_on(monkeypatch):
    monkeypatch.setattr(config, "STORM_COALESCE", True)


# --- flag OFF: byte-identical to v4 (the honest ugly baseline) --------------------------
def test_flag_off_no_lease_no_attach():
    autonomy.set_level(SVC, "L3")
    res = run_self_heal("inc-1", SVC)
    assert res["status"] == "mitigated"
    assert state_store.get_service_heal(SVC) is None  # no correlation lease created at all


# --- flag ON: the leader runs a full heal and records its settled outcome ---------------
def test_leader_runs_and_settles_released(storm_on):
    autonomy.set_level(SVC, "L3")
    res = run_self_heal("leader-inc", SVC)
    assert res["status"] == "mitigated"
    doc = state_store.get_service_heal(SVC)
    assert doc["leader_incident_id"] == "leader-inc"
    assert doc["outcome"] == "mitigated" and doc["lease_until"] <= time.time()  # settled -> released


# --- flag ON: a follower coalesces onto the live leader ---------------------------------
def test_follower_attaches_before_triage(storm_on):
    autonomy.set_level(SVC, "L3")
    assert state_store.claim_service_heal(SVC, "leader-inc", 600)[0] == "leader"  # leader mid-run
    res = run_self_heal("follower-inc", SVC)
    assert res["status"] == "attached" and res["leader_incident_id"] == "leader-inc"
    stages = [e.get("stage") for e in res.get("events", [])]
    assert stages == ["ATTACHED"]  # no TRIAGED, no probe, no ROLLBACK_APPLIED — the anti-amplification point
    rec = incidents.get("follower-inc")
    assert rec["status"] == "attached" and rec["leader_incident_id"] == "leader-inc"
    assert "follower-inc" in state_store.get_service_heal(SVC)["attached"]  # no lost id
    assert pending.get_pending(SVC) is None  # the follower never touched prod


def test_follower_redelivery_is_duplicate(storm_on):
    """An attached follower's OWN redelivery no-ops (its per-incident heal was finished)."""
    state_store.claim_service_heal(SVC, "leader-inc", 600)
    assert run_self_heal("follower-inc", SVC)["status"] == "attached"
    assert run_self_heal("follower-inc", SVC)["status"] == "duplicate"


# --- flag ON: the L1 lifecycle — hold while awaiting, release on the human decision ------
def test_awaiting_approval_holds_then_releases_on_approve(storm_on):
    autonomy.set_level(SVC, "L1")
    assert run_self_heal("leader-inc", SVC)["status"] == "awaiting_approval"
    held = state_store.get_service_heal(SVC)
    assert held["outcome"] == "awaiting_approval" and held["lease_until"] > time.time()  # HELD
    assert run_self_heal("refire-inc", SVC)["status"] == "attached"  # a re-fire coalesces (no 2nd card)
    assert autonomy.pending_approvals() and len(autonomy.pending_approvals()) == 1  # exactly ONE card
    assert apply_approval("leader-inc", approve=True)["status"] == "mitigated"
    rel = state_store.get_service_heal(SVC)
    assert rel["outcome"] == "mitigated" and rel["lease_until"] <= time.time()  # RELEASED on decision
    assert state_store.claim_service_heal(SVC, "fresh", 600)[0] == "leader"  # a new outage -> fresh leader


def test_denied_releases_the_lease(storm_on):
    autonomy.set_level(SVC, "L1")
    run_self_heal("leader-inc", SVC)
    assert apply_approval("leader-inc", approve=False)["status"] == "denied"
    doc = state_store.get_service_heal(SVC)
    assert doc["outcome"] == "denied" and doc["lease_until"] <= time.time()  # released, not held


def test_l2_fix_approval_holds_until_decided(storm_on):
    """L2 auto-rolls-back (bleeding stopped) but the fix-PR gate is a HELD state — the lease stays
    live so a late re-fire coalesces rather than spawning a second rollback; the L2 fix-PR DECISION
    then releases the lease through apply_approval (the release path 1.1 owns for L2)."""
    autonomy.set_level(SVC, "L2")
    assert run_self_heal("leader-inc", SVC)["status"] == "awaiting_fix_approval"
    doc = state_store.get_service_heal(SVC)
    assert doc["outcome"] == "awaiting_fix_approval" and doc["lease_until"] > time.time()  # HELD
    assert run_self_heal("refire-inc", SVC)["status"] == "attached"
    assert apply_approval("leader-inc", approve=True)["status"] == "mitigated"
    rel = state_store.get_service_heal(SVC)
    assert rel["outcome"] == "mitigated" and rel["lease_until"] <= time.time()  # RELEASED on the decision
    assert state_store.claim_service_heal(SVC, "fresh", 600)[0] == "leader"  # a new outage -> fresh leader


# --- flag ON: terminal-failed + bare-escalate RELEASE (never attach to a corpse) — §3 1.1 ---------
def test_exhausted_leader_releases_lease_for_takeover(storm_on, monkeypatch):
    """A leader whose heal EXHAUSTS its retry budget must RELEASE the correlation lease so the next
    alert for the still-broken service becomes a FRESH leader — never attaches to a corpse (§3 1.1).
    (Regression guard: run_self_heal's exhausted early-return skipped settle_service_heal.)"""
    autonomy.set_level(SVC, "L3")
    monkeypatch.setattr(config, "MAX_HEAL_ATTEMPTS", 2)

    def boom(incident_id, service):
        raise RuntimeError("transient backend fault")
    monkeypatch.setattr("autosre.state_machine._heal_body", boom)

    for _ in range(config.MAX_HEAL_ATTEMPTS):            # attempts 1..2: claimed -> _heal_body raises
        with pytest.raises(RuntimeError):
            run_self_heal("leader-inc", SVC)
    assert run_self_heal("leader-inc", SVC)["status"] == "manual_intervention"  # circuit breaker trips
    doc = state_store.get_service_heal(SVC)
    assert doc["outcome"] == "manual_intervention" and doc["lease_until"] <= time.time()  # RELEASED
    assert state_store.claim_service_heal(SVC, "fresh-inc", 600)[0] == "leader"  # fresh takeover, no corpse


def test_deadend_escalate_releases_lease(storm_on, monkeypatch):
    """A BARE escalate (reversibility BLOCK) armed NO approval and NO pending — nothing a re-fire can
    coalesce onto — so the lease RELEASES; a re-fire re-triages as a fresh leader, not a corpse-attach.
    (Spec §3 1.1: hold only 'escalated/awaiting WITH a live approval/pending state'.)"""
    autonomy.set_level(SVC, "L3")
    monkeypatch.setattr(config, "REVERSIBILITY_GUARD_ENABLED", True)
    mock.declare_irreversible()  # the serving (bad) revision declares a forward-only change -> BLOCK
    assert run_self_heal("leader-inc", SVC)["status"] == "escalated"
    assert pending.get_pending(SVC) is None and not autonomy.pending_approvals()  # nothing armed
    doc = state_store.get_service_heal(SVC)
    assert doc["outcome"] == "escalated" and doc["lease_until"] <= time.time()  # RELEASED (no corpse)
    assert state_store.claim_service_heal(SVC, "refire", 600)[0] == "leader"  # fresh leader, re-triage


def test_verify_fail_escalate_holds_lease(storm_on, monkeypatch):
    """The verify-FAILURE escalate armed a pending revert (the outage is still unsettled) — so the
    lease HOLDS and a late re-fire coalesces rather than re-rolling-back a still-broken service. This
    is the ONE escalate that legitimately holds, distinguished from the bare escalates by a live pending."""
    autonomy.set_level(SVC, "L3")
    monkeypatch.setattr("autosre.state_machine._verify", lambda *a, **k: False)  # rollback doesn't clear
    assert run_self_heal("leader-inc", SVC)["status"] == "escalated"
    assert pending.get_pending(SVC) is not None  # a pending revert WAS armed -> a live thing to coalesce onto
    doc = state_store.get_service_heal(SVC)
    assert doc["outcome"] == "escalated" and doc["lease_until"] > time.time()  # HELD
    assert run_self_heal("refire", SVC)["status"] == "attached"  # coalesces, doesn't re-roll-back


def test_complete_rollback_close_releases_held_lease(storm_on):
    """A held lease (from a verify-fail escalate) must RELEASE when complete_rollback CLOSES the
    transaction — otherwise the resolved outage keeps absorbing new alerts until the backstop lapses."""
    autonomy.set_level(SVC, "L3")
    # simulate the end-state of a verify-fail escalate: lease HELD + a pending revert armed
    state_store.claim_service_heal(SVC, "leader-inc", 900)
    state_store.settle_service_heal(SVC, "leader-inc", "escalated", config.APPROVAL_TTL_S)
    pending.set_pending(SVC, {"incident_id": "leader-inc", "bad_revision": f"{SVC}-00002-bad",
                              "rolled_back_to": f"{SVC}-00001-good", "rollback_at_epoch": time.time()})
    assert state_store.get_service_heal(SVC)["lease_until"] > time.time()  # held
    mock.deploy_fix()
    assert complete_rollback(SVC)["status"] == "closed"
    doc = state_store.get_service_heal(SVC)
    assert doc["outcome"] == "closed" and doc["lease_until"] <= time.time()  # RELEASED on close
