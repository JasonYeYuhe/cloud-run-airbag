"""The decision heuristic + the safety gate (_validate) + the ESCALATE surfacing — the logic
that decides whether the agent touches prod. Pure where possible; live paths exercised manually."""
from autosre import incidents, state_machine
from autosre.backends import mock
from autosre.state_machine import _heuristic, _validate, run_self_heal


def _revs(serving_traffic=100):
    return {"revisions": [
        {"name": "svc-00002-bad", "traffic_percent": serving_traffic, "ready": True},
        {"name": "svc-00001-good", "traffic_percent": 100 - serving_traffic, "ready": True}]}


def test_heuristic_rolls_back_serving_to_healthy():
    d = _heuristic(_revs(), {"error_rate": 0.5})
    assert d["action"] == "ROLLBACK"
    assert d["bad_revision"] == "svc-00002-bad" and d["rollback_revision"] == "svc-00001-good"


def test_heuristic_observes_when_no_errors():
    assert _heuristic(_revs(), {"error_rate": 0.0})["action"] == "OBSERVE"


def test_validate_escalates_below_confidence_threshold():
    d = {"action": "ROLLBACK", "rollback_revision": "svc-00001-good", "confidence": 0.1}
    assert _validate(d, _revs())["action"] == "ESCALATE"


def test_validate_escalates_unknown_rollback_target():
    d = {"action": "ROLLBACK", "rollback_revision": "ghost-rev", "confidence": 0.99}
    assert _validate(d, _revs())["action"] == "ESCALATE"


def test_validate_passes_known_confident_rollback():
    d = {"action": "ROLLBACK", "rollback_revision": "svc-00001-good", "confidence": 0.99}
    assert _validate(d, _revs())["action"] == "ROLLBACK"


def test_run_self_heal_surfaces_escalation(monkeypatch):
    """A gate failure must surface as ESCALATED + status=escalated, not a silent no-op."""
    mock.reset()
    monkeypatch.setattr(state_machine, "_heuristic", lambda revs, err: {
        "action": "ROLLBACK", "rollback_revision": "ghost-rev", "confidence": 0.99,
        "bad_revision": "svc-00002-bad", "_source": "test"})
    res = run_self_heal("inc-esc", "airbag-target")
    assert res["status"] == "escalated"
    rec = incidents.get("inc-esc")
    assert rec["status"] == "escalated"
    assert any(e["stage"] == "ESCALATED" for e in rec["events"])
