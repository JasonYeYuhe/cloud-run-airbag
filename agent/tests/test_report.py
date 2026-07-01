"""The incident-report Artifact renderer (report.py), incl. the v3 legibility surfacing (Phase 3.1):
the multi-signal per-detector breakdown, the causal pre-check verdict, and the Alert-to-Verified-
Recovery time — all extracted from the already-emitted events, so it works enabled OR default."""
from autosre import report


def _rec(events, **extra):
    return {"incident_id": "inc-x", "service": "svc", "status": "mitigated",
            "decision": {"action": "ROLLBACK", "confidence": 0.9}, "events": events, **extra}


def test_report_surfaces_multisignal_causal_and_recovery_time():
    events = [
        {"stage": "RECEIVED", "ts": 100.0, "msg": "incident"},
        {"stage": "ANALYZED", "ts": 101.0, "msg": "verdict FAIL", "verdict": "FAIL",
         "reason": "latency: 4/4 windows over SLO",
         "signals": {"5xx": {"verdict": "INCONCLUSIVE", "reason": "0/20"},
                     "latency": {"verdict": "FAIL", "reason": "4/4 windows over SLO"}}},
        {"stage": "CAUSAL", "ts": 102.0, "msg": "CAUSAL — target not degraded", "verdict": "CAUSAL"},
        {"stage": "ROLLBACK_APPLIED", "ts": 103.0, "msg": "-> good"},
        {"stage": "MITIGATED", "ts": 130.0, "msg": "recovered"},
    ]
    h = report.render(_rec(events))
    assert "multi-signal verdict" in h.lower()
    assert "latency" in h and "4/4 windows over SLO" in h        # per-detector breakdown
    assert "Causal pre-check" in h and "CAUSAL" in h             # causal verdict surfaced
    assert "alert → verified recovery" in h and "30s" in h       # 130 - 100 = 30s


def test_report_default_5xx_has_no_causal_card_but_shows_verdict():
    events = [
        {"stage": "RECEIVED", "ts": 100.0, "msg": "incident"},
        {"stage": "ANALYZED", "ts": 101.0, "msg": "FAIL", "verdict": "FAIL",
         "reason": "5xx confidently elevated", "rate": 0.7},
        {"stage": "MITIGATED", "ts": 120.0, "msg": "recovered"},
    ]
    h = report.render(_rec(events))
    assert "FAIL" in h and "5xx confidently elevated" in h
    assert "Causal pre-check" not in h        # no CAUSAL event -> no causal card
    assert "20s" in h                         # recovery time still computed


def test_report_no_events_does_not_crash():
    h = report.render({"incident_id": "i", "service": "s", "status": "noop", "events": []})
    assert "Airbag" in h and "alert → verified recovery" not in h


def test_report_escapes_event_content():
    events = [{"stage": "ANALYZED", "ts": 1.0, "msg": "<script>alert(1)</script>",
               "verdict": "<b>x</b>", "reason": "<i>y</i>"}]
    h = report.render(_rec(events))
    assert "<script>alert(1)</script>" not in h and "&lt;script&gt;" in h