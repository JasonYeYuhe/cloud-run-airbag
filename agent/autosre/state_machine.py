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

from . import adk_brain, config, events, gemini, pending, tools

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
    pr_url = None
    if github_pr.available():
        ctx = (f"bad revision {decision.get('bad_revision')} on {service} returned HTTP 500 on the "
               f"business path {config.PROBE_PATH} (unhandled exception, not an explicit error "
               f"response); evidence: {decision.get('evidence')}")
        pr = github_pr.open_fix_pr(service, ctx)
        if pr:
            pr_url = pr["pr_url"]
            emit("FIX_PR", f"opened fix PR — {pr['summary']}", pr_url=pr_url)
        else:
            emit("FIX_PR", "no fix PR opened (no change or error)")
    else:
        emit("FIX_PR", "fix-PR slow path not configured (set GITHUB_TOKEN/GITHUB_REPO)")

    # Remember the temporary rollback so it can be UNDONE once the fix deploys + verifies
    # (the fix-PR's CI calls /internal/complete-rollback; or the dashboard's Verify & Undo).
    pending.set_pending(service, {
        "incident_id": incident_id, "bad_revision": decision.get("bad_revision"),
        "rolled_back_to": target, "rollback_at_epoch": rollback_at, "pr_url": pr_url})
    emit("PENDING_REVERT", "rollback held until the fix deploys + is verified",
         rolled_back_to=target, pr_url=pr_url)

    return {"status": "mitigated", "incident_id": incident_id,
            "rolled_back_to": target, "events": run_events}


# --- P1: close the transaction (undo the temporary rollback once the fix ships) --------
def complete_rollback(service: str, fix_revision: str | None = None,
                      git_sha: str | None = None, pr_url: str | None = None) -> dict:
    """Undo the temporary rollback after the fix deploys. Verify the candidate IS the fix (a
    new, post-rollback, healthy revision — or the exact revision CI deployed), restore traffic
    to it, and CLOSE the transaction. On failure, compensate by routing back to the safe
    rolled-back revision and escalate (never loop)."""
    rec = pending.try_begin_complete(service)
    if rec is None:
        return {"status": "noop",
                "reason": "no pending rollback (or a completion is already running)",
                "service": service}
    incident_id = rec.get("incident_id", "unknown")
    run_events: list[dict] = []

    def emit(stage: str, msg: str, **data):
        log.info("[%s] %s %s", stage, msg, data or "")
        ev = events.publish({"incident_id": incident_id, "service": service,
                             "stage": stage, "msg": msg, **data})
        run_events.append(ev)

    closed = False
    # Cap compensation retries: after MAX failed undos, stop (traffic is already on the safe
    # revision) and require a human — don't keep re-shifting traffic on every re-trigger.
    if rec.get("attempts", 0) >= config.MAX_UNDO_ATTEMPTS:
        emit("MANUAL_INTERVENTION",
             f"giving up after {rec.get('attempts')} failed undo attempts — traffic stays on the "
             f"safe revision {rec.get('rolled_back_to')}; needs a human")
        pending.clear_pending(service)  # terminal: no further auto-undo
        return {"status": "manual_intervention", "reason": "max attempts", "terminal": True,
                "incident_id": incident_id, "events": run_events}
    try:
        emit("COMPLETE_ROLLBACK",
             f"fix reported (revision={fix_revision or 'auto'}, sha={git_sha or '—'}); "
             f"verifying before restoring traffic")
        revs = tools.list_cloud_run_revisions(service, config.GCP_REGION)
        candidate = _select_fix_revision(revs, rec, fix_revision)
        if not candidate:
            emit("MANUAL_INTERVENTION",
                 "no healthy fix revision found after the rollback — not restoring traffic")
            return {"status": "manual_intervention", "reason": "no candidate fix revision",
                    "incident_id": incident_id, "events": run_events}
        emit("FIX_DEPLOYED", f"candidate fix revision: {candidate}",
             revision=candidate, git_sha=git_sha, pr_url=pr_url or rec.get("pr_url"))

        # Restore traffic to the fix, then PROVE it's healthy; compensate if it isn't.
        tools.rollback_traffic_to_revision(service, config.GCP_REGION, candidate)
        restore_at = time.time()
        emit("REVERIFYING", f"100% traffic -> {candidate}; proving the fix is healthy")
        if _verify(service, emit, since_epoch=restore_at):
            emit("ROLLBACK_UNDONE",
                 f"temporary rollback undone — traffic restored to the fix ({candidate})")
            emit("CLOSED", "incident closed: rolled back, fixed, and traffic restored to the fix")
            closed = True
            return {"status": "closed", "restored_to": candidate,
                    "incident_id": incident_id, "events": run_events}
        # Compensating action: route back to the known-safe rolled-back revision.
        safe = rec.get("rolled_back_to")
        tools.rollback_traffic_to_revision(service, config.GCP_REGION, safe)
        attempts = pending.bump_attempts(service)
        emit("MANUAL_INTERVENTION",
             f"fix revision {candidate} failed verification (attempt {attempts}/"
             f"{config.MAX_UNDO_ATTEMPTS}) — compensated: traffic back on safe revision {safe}")
        return {"status": "compensated", "safe_revision": safe, "attempts": attempts,
                "incident_id": incident_id, "events": run_events}
    finally:
        pending.end_complete(service, closed=closed)


def _select_fix_revision(revs: dict, rec: dict, fix_revision: str | None) -> str | None:
    """The fix is either the exact revision CI deployed, or the newest READY revision that isn't
    the bad or rolled-back-to revision — preferring one created after the rollback (clock-skew
    tolerant), but falling back to the newest eligible so a legit fix is never falsely rejected
    over a few seconds of skew (verification is the real safety gate either way)."""
    ready = {r["name"] for r in revs.get("revisions", []) if r.get("ready")}
    bad, safe = rec.get("bad_revision"), rec.get("rolled_back_to")
    if fix_revision:  # CI told us exactly which revision is the fix
        return fix_revision if (fix_revision in ready and fix_revision not in (bad, safe)) else None
    eligible = [r for r in revs.get("revisions", [])
                if r.get("ready") and r["name"] not in (bad, safe)]
    eligible.sort(key=lambda r: _epoch(r.get("create_time")), reverse=True)
    rollback_at = rec.get("rollback_at_epoch") or 0.0
    after = [r for r in eligible if _epoch(r.get("create_time")) > rollback_at - _SKEW_S]
    chosen = after or eligible
    return chosen[0]["name"] if chosen else None


_SKEW_S = 120.0  # clock-skew tolerance for "created after the rollback"


def _epoch(iso: str | None) -> float:
    if not iso:
        return 0.0
    try:
        import datetime as _dt
        return _dt.datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()
    except Exception:  # noqa: BLE001
        return 0.0


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
