"""The decision heuristic + the safety gate (_validate) + the ESCALATE surfacing — the logic
that decides whether the agent touches prod. Pure where possible; live paths exercised manually."""
import pytest
from pydantic import ValidationError

from autosre import config, incidents, state_machine
from autosre.backends import mock
from autosre.schemas import IncidentDecision
from autosre.state_machine import _heuristic, _validate, run_self_heal


def test_schema_rejects_open_fix_pr():
    """Phase 0.3: OPEN_FIX_PR is no longer a top-level action — it silently became a no-op DONE that
    polluted the learned baseline. The fix-PR is a downstream step of ROLLBACK, not a chosen action."""
    with pytest.raises(ValidationError):
        IncidentDecision(action="OPEN_FIX_PR", confidence=0.9)


def test_schema_accepts_the_three_valid_actions():
    for a in ("ROLLBACK", "OBSERVE", "ESCALATE"):
        assert IncidentDecision(action=a, confidence=0.5).action == a


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


def test_validate_promotes_observe_to_rollback_on_stat_fail():
    """Phase 1.1b: a DETERMINISTIC FAIL verdict promotes an OBSERVE decision to a rollback (the
    multi-signal win) — the FSM acts on the statistical signal even when the LLM/heuristic hedged."""
    out = _validate({"action": "OBSERVE", "confidence": 0.4}, _revs(),
                    {"verdict": "FAIL", "reason": "latency p99 5x baseline"})
    assert out["action"] == "ROLLBACK"
    assert out["bad_revision"] == "svc-00002-bad" and out["rollback_revision"] == "svc-00001-good"
    assert out.get("_promoted") is True
    assert out["confidence"] >= config.CONFIDENCE_THRESHOLD


def test_validate_promotion_escalates_when_no_target():
    """FAIL but nowhere safe to go -> ESCALATE (was a silent OBSERVE miss)."""
    revs = {"revisions": [{"name": "svc-00002-bad", "traffic_percent": 100, "ready": True},
                          {"name": "svc-00001-old", "traffic_percent": 0, "ready": False}]}
    out = _validate({"action": "OBSERVE", "confidence": 0.4}, revs, {"verdict": "FAIL", "reason": "x"})
    assert out["action"] == "ESCALATE"


def test_validate_does_not_promote_on_inconclusive():
    """INCONCLUSIVE never promotes an OBSERVE — a healthy service (INCONCLUSIVE 5xx) stays a quiet
    OBSERVE, no alert fatigue."""
    out = _validate({"action": "OBSERVE", "confidence": 0.4}, _revs(),
                    {"verdict": "INCONCLUSIVE", "reason": "x"})
    assert out["action"] == "OBSERVE"


def _rollback():
    return {"action": "ROLLBACK", "rollback_revision": "svc-00001-good", "confidence": 0.99}


def test_stat_gate_allows_rollback_on_fail():
    d = _validate(_rollback(), _revs(), {"verdict": "FAIL", "reason": "elevated"})
    assert d["action"] == "ROLLBACK"


def test_stat_gate_withholds_rollback_on_pass():
    d = _validate(_rollback(), _revs(), {"verdict": "PASS", "reason": "healthy"})
    assert d["action"] == "OBSERVE"  # statistically healthy -> don't roll back


def test_stat_gate_escalates_rollback_on_inconclusive():
    d = _validate(_rollback(), _revs(), {"verdict": "INCONCLUSIVE", "reason": "too few"})
    assert d["action"] == "ESCALATE"  # not enough evidence to auto-act


def test_stat_pass_overrides_a_hallucinated_target():
    # PASS + invalid target -> OBSERVE (healthy service; don't escalate over the LLM's bad target)
    d = _validate({"action": "ROLLBACK", "rollback_revision": "ghost", "confidence": 0.99},
                  _revs(), {"verdict": "PASS", "reason": "healthy"})
    assert d["action"] == "OBSERVE"


def test_stat_fail_with_bad_target_escalates():
    d = _validate({"action": "ROLLBACK", "rollback_revision": "ghost", "confidence": 0.99},
                  _revs(), {"verdict": "FAIL", "reason": "elevated"})
    assert d["action"] == "ESCALATE"  # broken AND bad target -> page a human


def test_stat_gate_is_constraint_only_leaves_observe_alone():
    d = _validate({"action": "OBSERVE", "confidence": 0.4}, _revs(),
                  {"verdict": "INCONCLUSIVE", "reason": "x"})
    assert d["action"] == "OBSERVE"  # gate only constrains ROLLBACK -> no alert fatigue


def test_run_self_heal_surfaces_escalation(monkeypatch):
    """A gate failure must surface as ESCALATED + status=escalated, not a silent no-op."""
    mock.reset()
    monkeypatch.setattr(state_machine, "_heuristic", lambda revs, err, witnessed=None: {
        "action": "ROLLBACK", "rollback_revision": "ghost-rev", "confidence": 0.99,
        "bad_revision": "svc-00002-bad", "_source": "test"})
    res = run_self_heal("inc-esc", "airbag-target")
    assert res["status"] == "escalated"
    rec = incidents.get("inc-esc")
    assert rec["status"] == "escalated"
    assert any(e["stage"] == "ESCALATED" for e in rec["events"])
