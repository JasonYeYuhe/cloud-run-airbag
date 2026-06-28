"""P1 'undo the temporary rollback' transaction: after a heal records a pending revert, the
fix deploys and complete_rollback verifies it IS the fix, restores traffic, and CLOSES — or
compensates back to the safe revision on failure. Never loops; idempotent."""
from autosre import config, pending
from autosre.backends import mock
from autosre.state_machine import complete_rollback, run_self_heal

SVC = "airbag-target"


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


def test_idempotent_noop_without_pending():
    assert complete_rollback(SVC)["status"] == "noop"  # nothing pending
    _heal_then_pending()
    mock.deploy_fix()
    assert complete_rollback(SVC, fix_revision=f"{SVC}-00003-fix")["status"] == "closed"
    assert complete_rollback(SVC)["status"] == "noop"  # already closed -> no double-undo
