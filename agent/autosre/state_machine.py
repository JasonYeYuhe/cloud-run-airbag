"""Deterministic self-heal state machine.

This is where production actions execute. The LLM (Gemini, via `decide`) is only
asked to choose an action; it never calls a prod tool directly. Every stage emits
an auditable event (the "thought-chain" the dashboard will replay).

Stages: RECEIVED → TRIAGED → DECISION → ROLLBACK_APPLIED → VERIFYING
        → MITIGATED → (stretch) FIX_PR  |  OBSERVE/ESCALATED
"""
from __future__ import annotations

import logging
import time

from . import config, tools

log = logging.getLogger("airbag.sm")


def run_self_heal(incident_id: str, service: str) -> dict:
    events: list[dict] = []

    def emit(stage: str, msg: str, **data):
        log.info("[%s] %s %s", stage, msg, data or "")
        events.append({"stage": stage, "msg": msg, **data})

    emit("RECEIVED", f"incident {incident_id} on {service}")

    # --- TRIAGE: gather evidence ------------------------------------------
    revs = tools.list_cloud_run_revisions(service, config.GCP_REGION)
    err = tools.query_error_rate(service, config.GCP_REGION, window_minutes=5)
    emit("TRIAGED", "collected revisions + error rate",
         error_rate=err.get("error_rate"), revisions=revs.get("revisions"))

    # --- DECISION (TODO: replace heuristic with Gemini responseSchema) -----
    decision = decide(revs, err)
    emit("DECISION", decision["action"], **decision)
    if decision["action"] != "ROLLBACK":
        emit("DONE", "no rollback needed")
        return {"status": "noop", "incident_id": incident_id, "events": events}

    # --- ROLLBACK: deterministic stop-the-bleeding ------------------------
    target = decision["rollback_revision"]
    result = tools.rollback_traffic_to_revision(service, config.GCP_REGION, target)
    emit("ROLLBACK_APPLIED", f"100% traffic -> {target}", result=result)

    # --- VERIFY: error-rate -> 0 AND synthetic probe ok -------------------
    if not _verify(service, emit):
        emit("ESCALATED", "rollback did not clear errors within budget")
        return {"status": "escalated", "incident_id": incident_id, "events": events}
    emit("MITIGATED", "error rate back to zero — recovery proven")

    # --- FIX PR (stretch, see docs/PLAN.md step 9) ------------------------
    emit("FIX_PR", "stretch: Gemini-authored fix PR + real CI not yet wired")

    return {"status": "mitigated", "incident_id": incident_id,
            "rolled_back_to": target, "events": events}


def decide(revs: dict, err: dict) -> dict:
    """Day-0 deterministic heuristic. Replace with a Gemini structured decision
    (IncidentDecision schema in agent.py) once the model path is wired."""
    rs = revs.get("revisions", [])
    serving = next((r for r in rs if r.get("traffic_percent", 0) > 0), None)
    healthy = next((r for r in rs if r.get("traffic_percent", 0) == 0 and r.get("ready")), None)
    if err.get("error_rate", 0) >= config.ERROR_RATE_THRESHOLD and serving and healthy:
        return {"action": "ROLLBACK", "bad_revision": serving["name"],
                "rollback_revision": healthy["name"], "confidence": 0.9,
                "evidence": [f"5xx rate {err.get('error_rate')} on {serving['name']}"]}
    return {"action": "OBSERVE", "confidence": 0.4, "evidence": []}


def _verify(service: str, emit, attempts: int = 6, interval_s: int = 5) -> bool:
    """Poll until error-rate is zero AND a synthetic probe succeeds (guards the
    zero-traffic trap: error_rate can read 0 simply because nothing is hitting it)."""
    for i in range(attempts):
        err = tools.query_error_rate(service, config.GCP_REGION, window_minutes=2)
        probe = tools.synthetic_probe(service)
        emit("VERIFYING", f"attempt {i + 1}/{attempts}",
             error_rate=err.get("error_rate"), total_requests=err.get("total_requests"),
             probe_ok=probe.get("ok"))
        if probe.get("ok") and err.get("error_rate", 1) == 0:
            return True
        if not config.USE_MOCK:
            time.sleep(interval_s)
    return False
