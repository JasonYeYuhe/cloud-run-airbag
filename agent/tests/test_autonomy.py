"""Graduated autonomy — the per-service levels enforced in the state machine, the durable L1/L2
approval gate, and the advisory trust ramp. Mock backend; conftest pins memory store + resets it."""
import pytest

from autosre import autonomy, config, pending
from autosre.backends import mock
from autosre.state_machine import apply_approval, run_self_heal


def setup_function(_):
    mock.reset()  # bad revision serving -> a ROLLBACK decision
    config.GEMINI_API_KEY = ""


# --- level config + trust ramp -------------------------------------------------------
def test_default_level_and_set():
    assert autonomy.level_for("svc") == config.AUTONOMY_LEVEL  # L3 default
    autonomy.set_level("svc", "L1")
    assert autonomy.level_for("svc") == "L1"


def test_invalid_level_rejected():
    with pytest.raises(ValueError):
        autonomy.set_level("svc", "L9")


def test_trust_ramp_advises_promotion_from_l1(monkeypatch):
    monkeypatch.setattr(config, "AUTONOMY_PROMOTE_AFTER", 3)
    autonomy.set_level("svc", "L1")
    for _ in range(2):
        assert autonomy.record_outcome("svc", success=True)["advise_promote"] is False
    rec = autonomy.record_outcome("svc", success=True)  # 3rd success
    assert rec["advise_promote"] is True and rec["streak"] == 3


def test_failure_auto_demotes_autonomous_level():
    autonomy.set_level("svc", "L3")
    rec = autonomy.record_outcome("svc", success=False)
    assert rec["level"] == "L1" and rec["demoted_from"] == "L3" and rec["streak"] == 0


# --- v5 Phase 1.3: demotion breadcrumb bookkeeping (AIRBAG_APPROVAL_COALESCE) ---------
def test_demotion_breadcrumb_erased_when_flag_off():
    """Flag OFF -> byte-identical v2: demoted_from is ephemeral (erased on the next record_outcome)."""
    autonomy.set_level("svc", "L3")
    assert autonomy.record_outcome("svc", success=False)["demoted_from"] == "L3"  # the demotion
    rec = autonomy.record_outcome("svc", success=False)  # a later L1 failure
    assert rec.get("demoted_from") is None  # v2 erased the breadcrumb (the ergonomics bug)


def test_demotion_breadcrumb_preserved_when_flag_on(monkeypatch):
    """Flag ON -> the breadcrumb + CAUSING incident survive later L1 failures (the storm's step 3:
    'why am I at L1' must not vanish the moment a second storm heal fails)."""
    monkeypatch.setattr(config, "APPROVAL_COALESCE", True)
    autonomy.set_level("svc", "L3")
    rec = autonomy.record_outcome("svc", success=False, incident_id="inc-cause")
    assert rec["level"] == "L1" and rec["demoted_from"] == "L3" and rec["demoted_by_incident"] == "inc-cause"
    rec2 = autonomy.record_outcome("svc", success=False, incident_id="inc-later")  # already L1
    assert rec2["demoted_from"] == "L3" and rec2["demoted_by_incident"] == "inc-cause"  # not erased/overwritten
    rec3 = autonomy.record_outcome("svc", success=True, incident_id="inc-ok")  # a success at L1
    assert rec3["demoted_from"] == "L3" and rec3["demoted_by_incident"] == "inc-cause"  # still demoted until re-granted
    assert autonomy.status("svc")["demoted_by_incident"] == "inc-cause"  # surfaced to the operator


def test_re_grant_clears_demotion_breadcrumb(monkeypatch):
    monkeypatch.setattr(config, "APPROVAL_COALESCE", True)
    autonomy.set_level("svc", "L3")
    autonomy.record_outcome("svc", success=False, incident_id="inc-cause")  # demote L3 -> L1
    rec = autonomy.set_level("svc", "L3")  # a human explicitly re-grants
    assert rec.get("demoted_from") is None and rec.get("demoted_by_incident") is None


def test_approval_save_get_expire(monkeypatch):
    autonomy.save_approval("inc1", {"service": "svc", "kind": "rollback"})
    assert autonomy.get_approval("inc1")["kind"] == "rollback"
    monkeypatch.setattr(config, "APPROVAL_TTL_S", -1)  # next save expires immediately
    autonomy.save_approval("inc2", {"service": "svc", "kind": "rollback"})
    assert autonomy.get_approval("inc2") is None


# --- level enforcement in the heal flow ----------------------------------------------
def test_l0_observe_never_acts():
    autonomy.set_level("airbag-target", "L0")
    res = run_self_heal("inc-l0", "airbag-target")
    assert res["status"] == "observed"
    stages = [e["stage"] for e in res["events"]]
    assert "ROLLBACK_APPLIED" not in stages and "OBSERVE_ONLY" in stages


def test_l1_gates_rollback_then_approve_mitigates():
    autonomy.set_level("airbag-target", "L1")
    res = run_self_heal("inc-l1", "airbag-target")
    assert res["status"] == "awaiting_approval"
    assert "ROLLBACK_APPLIED" not in [e["stage"] for e in res["events"]]
    assert autonomy.get_approval("inc-l1")["kind"] == "rollback"  # durably queued
    done = apply_approval("inc-l1", approve=True)
    assert done["status"] == "mitigated"
    assert autonomy.get_approval("inc-l1") is None  # consumed


def test_l1_deny_does_not_act():
    autonomy.set_level("airbag-target", "L1")
    run_self_heal("inc-deny", "airbag-target")
    res = apply_approval("inc-deny", approve=False)
    assert res["status"] == "denied"


def test_l2_auto_rolls_back_but_gates_fix_pr():
    autonomy.set_level("airbag-target", "L2")
    res = run_self_heal("inc-l2", "airbag-target")
    assert res["status"] == "awaiting_fix_approval"
    stages = [e["stage"] for e in res["events"]]
    assert "ROLLBACK_APPLIED" in stages and "MITIGATED" in stages   # bleeding stopped
    assert pending.get_pending("airbag-target") is not None          # rollback held for undo
    assert autonomy.get_approval("inc-l2")["kind"] == "fix_pr"
    done = apply_approval("inc-l2", approve=True)
    assert done["status"] == "mitigated"


def test_l3_full_auto_unchanged():
    autonomy.set_level("airbag-target", "L3")
    res = run_self_heal("inc-l3", "airbag-target")
    assert res["status"] == "mitigated"  # original behavior


def test_apply_approval_noop_when_absent():
    assert apply_approval("ghost", approve=True)["status"] == "noop"


def test_claim_approval_is_single_winner():
    """A double-clicked Approve must resume the incident exactly once (no double rollback)."""
    import threading
    autonomy.save_approval("incX", {"service": "svc", "kind": "rollback"})
    wins, barrier = [], threading.Barrier(8)

    def w():
        barrier.wait()
        r = autonomy.claim_approval("incX")
        if r is not None:
            wins.append(r)

    threads = [threading.Thread(target=w) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(wins) == 1
