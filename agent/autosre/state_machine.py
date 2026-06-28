"""Deterministic self-heal state machine.

Production actions execute here. Gemini (gemini.decide) is asked only to choose an
action; the state machine validates it and acts. Every stage is published to the
event bus so the dashboard can replay it as a verifiable thought-chain.

Stages: RUN_START → RECEIVED → TRIAGED → DECISION → ROLLBACK_APPLIED → VERIFYING…
        → MITIGATED  |  OBSERVE → DONE  |  ESCALATED
"""
from __future__ import annotations

import logging
import time

from . import adk_brain, config, events, gemini, tools

log = logging.getLogger("airbag.sm")


def run_self_heal(incident_id: str, service: str) -> dict:
    run_events: list[dict] = []

    def emit(stage: str, msg: str, **data):
        log.info("[%s] %s %s", stage, msg, data or "")
        ev = events.publish({"incident_id": incident_id, "service": service,
                             "stage": stage, "msg": msg, **data})
        run_events.append(ev)

    emit("RUN_START", f"backend={config.BACKEND} gemini={'on' if gemini.available() else 'off'}")
    emit("RECEIVED", f"incident {incident_id} on {service}")

    # --- TRIAGE -----------------------------------------------------------
    revs = tools.list_cloud_run_revisions(service, config.GCP_REGION)
    err = tools.query_error_rate(service, config.GCP_REGION, window_minutes=5)
    before = dict(err)
    emit("TRIAGED", "collected revisions + error rate",
         error_rate=err.get("error_rate"), revisions=revs.get("revisions"))

    # --- DECISION: ADK SequentialAgent (Gemini calls the tools) -> direct Gemini -> heuristic --
    decision = adk_brain.decide(service)
    if decision:
        emit("ADK", f"ADK SequentialAgent (triage→decide) ran; "
                    f"tools called: {decision.get('_adk_tools') or '—'}")
    else:
        decision = gemini.decide(service, revs, err) or _heuristic(revs, err)
    decision = _validate(decision, revs)
    emit("DECISION", decision["action"], **decision)
    if decision["action"] != "ROLLBACK":
        emit("DONE", "no rollback needed")
        return {"status": "noop", "incident_id": incident_id, "events": run_events}

    # --- ROLLBACK (deterministic stop-the-bleeding) -----------------------
    target = decision["rollback_revision"]
    result = tools.rollback_traffic_to_revision(service, config.GCP_REGION, target)
    rollback_at = time.time()
    emit("ROLLBACK_APPLIED", f"100% traffic -> {target}", result=result)

    # --- VERIFY (error-rate -> 0 AND synthetic probe ok), measured from rollback --
    if not _verify(service, emit, since_epoch=rollback_at):
        emit("ESCALATED", "rollback did not clear errors within budget")
        return {"status": "escalated", "incident_id": incident_id, "events": run_events}

    after = tools.query_error_rate(service, config.GCP_REGION, window_minutes=2,
                                   since_epoch=rollback_at)
    note = gemini.explain_recovery(service, before, after)
    emit("MITIGATED", note or "error rate back to zero — recovery proven",
         before=before.get("error_rate"), after=after.get("error_rate"))

    # --- FIX PR (slow path): Gemini opens a real fix PR through CI ---------
    from . import github_pr
    if github_pr.available():
        ctx = (f"bad revision {decision.get('bad_revision')} on {service} returned HTTP 500 on the "
               f"business path {config.PROBE_PATH} (unhandled exception, not an explicit error "
               f"response); evidence: {decision.get('evidence')}")
        pr = github_pr.open_fix_pr(service, ctx)
        if pr:
            emit("FIX_PR", f"opened fix PR — {pr['summary']}", pr_url=pr["pr_url"])
        else:
            emit("FIX_PR", "no fix PR opened (no change or error)")
    else:
        emit("FIX_PR", "fix-PR slow path not configured (set GITHUB_TOKEN/GITHUB_REPO)")

    return {"status": "mitigated", "incident_id": incident_id,
            "rolled_back_to": target, "events": run_events}


def _heuristic(revs: dict, err: dict) -> dict:
    rs = revs.get("revisions", [])
    serving = next((r for r in rs if r.get("traffic_percent", 0) > 0), None)
    healthy = next((r for r in rs if r.get("traffic_percent", 0) == 0 and r.get("ready")), None)
    if err.get("error_rate", 0) >= config.ERROR_RATE_THRESHOLD and serving and healthy:
        return {"action": "ROLLBACK", "bad_revision": serving["name"],
                "rollback_revision": healthy["name"], "confidence": 0.9,
                "reasoning": f"5xx rate {err.get('error_rate')} on {serving['name']}; "
                             f"{healthy['name']} is a healthy prior revision.",
                "evidence": [f"error_rate={err.get('error_rate')}"], "_source": "heuristic"}
    return {"action": "OBSERVE", "confidence": 0.4,
            "reasoning": "no clear bad-revision/healthy-revision pair", "_source": "heuristic"}


def _validate(decision: dict, revs: dict) -> dict:
    """Safety gate: only act on a known-good revision above the confidence threshold."""
    if decision.get("action") != "ROLLBACK":
        return decision
    known = {r["name"] for r in revs.get("revisions", []) if r.get("ready")}
    target = decision.get("rollback_revision")
    if decision.get("confidence", 0) < config.CONFIDENCE_THRESHOLD or target not in known:
        return {**decision, "action": "ESCALATE",
                "reasoning": f"gate failed (confidence/target). {decision.get('reasoning', '')}"}
    return decision


def _verify(service: str, emit, since_epoch: float | None = None) -> bool:
    """Poll until error-rate is zero AND a synthetic probe succeeds (guards the
    zero-traffic trap: error_rate can read 0 simply because nothing is hitting it).
    `since_epoch` anchors the error window at rollback time (gcp backend)."""
    for i in range(config.VERIFY_ATTEMPTS):
        err = tools.query_error_rate(service, config.GCP_REGION, window_minutes=2,
                                     since_epoch=since_epoch)
        probe = tools.synthetic_probe(service, path=config.PROBE_PATH)
        emit("VERIFYING", f"attempt {i + 1}/{config.VERIFY_ATTEMPTS}",
             error_rate=err.get("error_rate"), total_requests=err.get("total_requests"),
             probe_ok=probe.get("ok"))
        if probe.get("ok") and err.get("error_rate", 1) == 0:
            return True
        time.sleep(config.VERIFY_INTERVAL_S)
    return False
