"""P1 'undo the temporary rollback' transaction: after a heal records a pending revert, the
fix deploys and complete_rollback verifies it IS the fix, restores traffic, and CLOSES — or
compensates back to the safe revision on failure. Never loops; idempotent."""
from autosre import autonomy, config, memory, pending
from autosre.backends import mock
from autosre.state_machine import complete_rollback, run_self_heal

SVC = "airbag-target"
_FIX = f"{SVC}-00003-fix"


def setup_function(_):
    mock.reset()
    pending.clear_pending(SVC)
    config.GEMINI_API_KEY = ""  # deterministic heuristic; no ADK/Gemini/GitHub


def _heal_then_pending():
    res = run_self_heal("inc-p1", SVC)
    assert res["status"] == "mitigated"
    assert pending.get_pending(SVC), "rollback should leave a pending revert"


def test_close_with_explicit_fix_revision():
    _heal_then_pending()
    mock.deploy_fix()
    res = complete_rollback(SVC, fix_revision=f"{SVC}-00003-fix", git_sha="abc123")
    assert res["status"] == "closed"
    assert res["restored_to"] == f"{SVC}-00003-fix"
    assert pending.get_pending(SVC) is None  # transaction cleared


def test_close_with_auto_selected_fix():
    _heal_then_pending()
    mock.deploy_fix()  # far-future create_time -> "after the rollback"
    res = complete_rollback(SVC)  # no explicit revision -> auto-select
    assert res["status"] == "closed"
    assert res["restored_to"].endswith("-fix")


def test_manual_intervention_when_no_fix_deployed():
    _heal_then_pending()
    res = complete_rollback(SVC)  # no fix revision exists yet
    assert res["status"] == "manual_intervention"
    assert pending.get_pending(SVC), "pending kept so a real fix can still close it later"


def test_compensates_when_fix_unhealthy(monkeypatch):
    _heal_then_pending()
    mock.deploy_fix()
    # the 'fix' is actually broken: probe never goes healthy -> verify fails -> compensate
    monkeypatch.setattr(config, "VERIFY_ATTEMPTS", 1)
    monkeypatch.setattr(config, "VERIFY_INTERVAL_S", 0)
    from autosre import tools
    monkeypatch.setattr(tools, "synthetic_probe",
                        lambda *a, **k: {"ok": False, "path": "/api/orders", "status": 500})
    res = complete_rollback(SVC, fix_revision=f"{SVC}-00003-fix")
    assert res["status"] == "compensated"
    assert res["safe_revision"] == f"{SVC}-00001-good"


def test_canary_restore_is_staged(monkeypatch):
    from autosre import tools
    calls = []

    def _rec(s, r, split, **kw):
        calls.append(dict(split))
        return mock.set_traffic_split(s, r, split)  # keep mock healthy so each gate passes

    monkeypatch.setattr(tools, "set_traffic_split", _rec)
    _heal_then_pending()
    mock.deploy_fix()
    res = complete_rollback(SVC, fix_revision=f"{SVC}-00003-fix")
    assert res["status"] == "closed"
    fix = f"{SVC}-00003-fix"
    assert [c.get(fix) for c in calls] == [10, 50, 100]  # gradual canary to the fix


def test_idempotent_noop_without_pending():
    assert complete_rollback(SVC)["status"] == "noop"  # nothing pending
    _heal_then_pending()
    mock.deploy_fix()
    assert complete_rollback(SVC, fix_revision=f"{SVC}-00003-fix")["status"] == "closed"
    assert complete_rollback(SVC)["status"] == "noop"  # already closed -> no double-undo


# --- v5 Phase 3.2: close-time settlement (AIRBAG_CLOSE_SETTLEMENT) --------------------------------
def test_close_flag_off_neither_witnesses_nor_credits():
    """Flag OFF -> byte-identical v4: CLOSED does NOT witness the fix or touch the trust ramp."""
    autonomy.set_level(SVC, "L3")
    _heal_then_pending()                          # mitigate credits streak -> 1, witnesses the SAFE rev
    streak = autonomy.status(SVC)["streak"]
    mock.deploy_fix()
    res = complete_rollback(SVC, fix_revision=_FIX)
    assert res["status"] == "closed"
    assert autonomy.status(SVC)["streak"] == streak          # unchanged (no close-time credit)
    assert _FIX not in memory.witnessed_healthy(SVC)         # the fix revision is NOT witnessed
    assert "TRUST_CREDIT" not in [e.get("stage") for e in res["events"]]


def test_close_settlement_witnesses_fix_without_double_counting(monkeypatch):
    """Flag ON, normal flow: CLOSED witnesses the fix revision but does NOT re-credit the trust ramp
    (the mitigate already counted this incident's success — outcome_counted guards the double-count)."""
    monkeypatch.setattr(config, "CLOSE_SETTLEMENT", True)
    autonomy.set_level(SVC, "L3")
    _heal_then_pending()
    streak = autonomy.status(SVC)["streak"]                  # credited once, at mitigate
    mock.deploy_fix()
    res = complete_rollback(SVC, fix_revision=_FIX)
    assert res["status"] == "closed"
    assert autonomy.status(SVC)["streak"] == streak          # NOT double-counted
    assert _FIX in memory.witnessed_healthy(SVC)             # the canary-survived fix is now witnessed


def test_close_settlement_credits_recovery_when_not_counted(monkeypatch):
    """Flag ON, recovery flow: a verify-FAIL mitigate never credited a success (outcome_counted unset),
    so CLOSED credits the trust ramp — the asymmetry fix (canary-FAIL demotes, canary-SUCCESS credits)."""
    monkeypatch.setattr(config, "CLOSE_SETTLEMENT", True)
    autonomy.set_level(SVC, "L3")
    autonomy.record_outcome(SVC, success=False, incident_id="inc-vf")   # a prior failure: streak 0
    assert autonomy.status(SVC)["streak"] == 0
    # the end-state of a verify-fail mitigate: pending armed WITHOUT outcome_counted
    pending.set_pending(SVC, {"incident_id": "inc-vf", "bad_revision": f"{SVC}-00002-bad",
                              "rolled_back_to": f"{SVC}-00001-good", "rollback_at_epoch": 1.0})
    mock.deploy_fix()
    res = complete_rollback(SVC, fix_revision=_FIX)
    assert res["status"] == "closed"
    assert autonomy.status(SVC)["streak"] == 1                          # recovery credited (0 -> 1)
    assert "TRUST_CREDIT" in [e.get("stage") for e in res["events"]]
    assert _FIX in memory.witnessed_healthy(SVC)
