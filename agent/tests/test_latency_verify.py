"""v3 Phase 3.4b: verification and remediation must be signal-aware.

We DETECT on multiple signals, so we must VERIFY on them too: a rollback onto a still-slow revision
would otherwise read "recovered" (200 + 0 5xx) while the very signal that triggered the incident is
still failing. And a latency regression's remedy IS the rollback — there's no HTTP 500 for a forward
code-fix PR to repair, so we don't fabricate one.
"""
from autosre import autonomy, config, incidents, state_machine
from autosre.backends import mock
from autosre.state_machine import _primary_signal, _verify, apply_approval, run_self_heal


def _force_latency_only(monkeypatch):
    """Make the signal engine + decision path produce a latency-only FAIL that the deterministic
    promotion turns into a rollback (the heuristic/LLM hedge because 5xx looks fine)."""
    monkeypatch.setattr(config, "SIGNALS", "all")
    monkeypatch.setattr(config, "STAT_GATE_ENABLED", True)
    monkeypatch.setattr(state_machine.signals, "detect", lambda *a, **k: {
        "verdict": "FAIL", "reason": "latency: 4/4 windows over SLO", "rate": None,
        "signals": {"5xx": {"verdict": "INCONCLUSIVE", "reason": "0/20"},
                    "latency": {"verdict": "FAIL", "reason": "4/4 windows over SLO"}}})
    monkeypatch.setattr(state_machine, "_heuristic", lambda revs, err: {
        "action": "OBSERVE", "confidence": 0.4, "_source": "test"})
    monkeypatch.setattr(state_machine.adk_brain, "decide", lambda *a, **k: None)
    monkeypatch.setattr(state_machine.gemini, "decide", lambda *a, **k: None)


# ---- _primary_signal: which detector drove the FAIL -------------------------------------------
def test_primary_signal_none_is_5xx():
    assert _primary_signal(None) == "5xx"


def test_primary_signal_single_detector_is_5xx():
    # single-detector (5xx) mode has no per-signal breakdown -> a 5xx incident
    assert _primary_signal({"verdict": "FAIL", "reason": "elevated"}) == "5xx"


def test_primary_signal_latency_only():
    stat = {"verdict": "FAIL", "signals": {
        "5xx": {"verdict": "INCONCLUSIVE"}, "latency": {"verdict": "FAIL"}}}
    assert _primary_signal(stat) == "latency"


def test_primary_signal_prefers_5xx_when_both_fail():
    # a real 5xx failure -> the code-fix PR path applies even if latency also fired
    stat = {"verdict": "FAIL", "signals": {
        "5xx": {"verdict": "FAIL"}, "latency": {"verdict": "FAIL"}}}
    assert _primary_signal(stat) == "5xx"


def test_primary_signal_unknown_when_no_single_culprit():
    # multi-detector FAIL but no signal individually FAILed -> 'unknown' (must NOT fabricate a 5xx fix)
    stat = {"verdict": "FAIL", "signals": {
        "5xx": {"verdict": "INCONCLUSIVE"}, "latency": {"verdict": "INCONCLUSIVE"}}}
    assert _primary_signal(stat) == "unknown"


# ---- _verify: recovery is proven on the TRIGGERING signal (gated on primary_signal, not "enabled") -
def _patch_verify(monkeypatch, *, elapsed_ms, error_rate=0.0):
    monkeypatch.setattr(config, "VERIFY_ATTEMPTS", 2)
    monkeypatch.setattr(config, "VERIFY_INTERVAL_S", 0)
    monkeypatch.setattr(state_machine.tools, "query_error_rate",
                        lambda *a, **k: {"error_rate": error_rate})
    monkeypatch.setattr(state_machine.tools, "synthetic_probe",
                        lambda *a, **k: {"ok": True, "status": 200, "elapsed_ms": elapsed_ms})


def test_verify_fails_slow_success_for_latency_incident(monkeypatch):
    # latency incident: 200 + 0 5xx but past the SLO -> the latency signal has NOT recovered
    _patch_verify(monkeypatch, elapsed_ms=config.LATENCY_SLO_ABS_MS + 500)
    assert _verify("svc", lambda *a, **k: None, primary_signal="latency") is False


def test_verify_passes_fast_success_for_latency_incident(monkeypatch):
    _patch_verify(monkeypatch, elapsed_ms=25.0)
    assert _verify("svc", lambda *a, **k: None, primary_signal="latency") is True


def test_verify_does_not_gate_latency_for_5xx_incident(monkeypatch):
    # a 5xx incident must NOT be falsely escalated because its last-good revision is a little slow
    _patch_verify(monkeypatch, elapsed_ms=config.LATENCY_SLO_ABS_MS + 500)
    assert _verify("svc", lambda *a, **k: None, primary_signal="5xx") is True


# ---- latency-only incident: rollback is the remedy, no forward fix-PR --------------------------
def test_latency_incident_skips_fix_pr(monkeypatch):
    """A latency FAIL heals via rollback; no HTTP-500 fix-PR is fabricated, and the fix-PR opener
    is never called."""
    mock.reset()
    _force_latency_only(monkeypatch)
    called = {"fix_pr": False}
    monkeypatch.setattr(state_machine, "_open_fix_pr",
                        lambda *a, **k: called.__setitem__("fix_pr", True) or "http://x")

    res = run_self_heal("inc-lat", "airbag-target")
    assert res["status"] == "mitigated"
    assert called["fix_pr"] is False, "no forward code-fix PR for a latency regression"
    rec = incidents.get("inc-lat")
    assert rec.get("pr_url") is None
    assert any(e["stage"] == "FIX_PR" and "no forward code-fix PR" in e.get("msg", "")
               for e in rec["events"])
    # it did roll back to the healthy revision ...
    assert any(e["stage"] == "ROLLBACK_APPLIED" for e in rec["events"])
    # ... and STILL armed the pending-revert (the rollback pinned traffic; the pin must be tracked
    # so a later healthy deploy isn't stranded at 0% — Gemini review finding).
    assert any(e["stage"] == "PENDING_REVERT" for e in rec["events"])


def test_l1_latency_approval_resume_skips_fix_pr(monkeypatch):
    """L1 resume: apply_approval must carry the latency signal into _mitigate (not the '5xx' default),
    so an approved latency rollback doesn't open a bogus HTTP-500 fix-PR (Gemini re-review finding)."""
    mock.reset()
    _force_latency_only(monkeypatch)
    autonomy.set_level("airbag-target", "L1")
    called = {"fix_pr": False}
    monkeypatch.setattr(state_machine, "_open_fix_pr",
                        lambda *a, **k: called.__setitem__("fix_pr", True) or "http://x")
    try:
        r1 = run_self_heal("inc-l1-lat", "airbag-target")
        assert r1["status"] == "awaiting_approval"
        r2 = apply_approval("inc-l1-lat", approve=True)
        assert r2["status"] == "mitigated"
        assert called["fix_pr"] is False, "L1 latency resume must not open a code-fix PR"
    finally:
        autonomy.set_level("airbag-target", config.AUTONOMY_LEVEL)
