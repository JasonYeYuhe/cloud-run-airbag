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

from . import (adk_brain, analyzer, autonomy, config, events, gemini, incidents, memory,
               pending, signals, state_store, tools)

log = logging.getLogger("airbag.sm")


def _incident_signature() -> str:
    """A coarse, stable failure fingerprint for recurrence detection across incidents."""
    return f"5xx:{config.PROBE_PATH}"


def run_self_heal(incident_id: str, service: str) -> dict:
    """Idempotent entry point: claim a per-incident lease BEFORE any side effect so a duplicate
    trigger (notably Cloud Tasks at-least-once redelivery) can't double-heal. Drop duplicates;
    release the lease on a transient failure so a retry re-runs; mark done on a clean finish."""
    claim = state_store.claim_heal(incident_id, config.HEAL_LEASE_S, config.DEDUP_TTL_S,
                                   config.MAX_HEAL_ATTEMPTS)
    if claim == "duplicate":
        log.info("[DUPLICATE] heal %s already running or done — dropping redelivery", incident_id)
        return {"status": "duplicate", "incident_id": incident_id}
    if claim == "exhausted":  # circuit breaker: stop redelivering a deterministically-failing heal
        log.error("[MANUAL_INTERVENTION] heal %s failed %d times — giving up; needs a human",
                  incident_id, config.MAX_HEAL_ATTEMPTS)
        incidents.record(incident_id, {"service": service, "status": "manual_intervention",
                                       "events": [{"stage": "MANUAL_INTERVENTION", "ts": time.time(),
                                                   "msg": f"heal failed {config.MAX_HEAL_ATTEMPTS}x — giving up",
                                                   "incident_id": incident_id, "service": service}]})
        return {"status": "manual_intervention", "incident_id": incident_id,
                "reason": f"heal failed {config.MAX_HEAL_ATTEMPTS}x"}
    try:
        result = _heal_body(incident_id, service)
    except Exception:
        state_store.release_heal(incident_id)  # transient failure -> let a retry re-claim (attempts bumped)
        raise
    state_store.finish_heal(incident_id, config.DEDUP_TTL_S)
    return result


def _heal_body(incident_id: str, service: str) -> dict:
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

    # --- STATISTICAL SIGNAL: multi-signal verdict gating ROLLBACK (v3 Phase 1). In the default
    # config (AIRBAG_SIGNALS=5xx) this is the v2 Wilson-CI 5xx verdict verbatim; enabling more signals
    # fuses latency/etc into the SAME FAIL/PASS/INCONCLUSIVE contract _validate consumes. baseline is
    # the per-service LEARNED 5xx rate (memory). STAT_GATE_ENABLED is the master switch (v2, unchanged).
    stat = None
    if config.STAT_GATE_ENABLED:
        stat = signals.detect(service, config.GCP_REGION, memory.baseline_for(service))
        emit("ANALYZED", f"statistical verdict {stat['verdict']} — {stat['reason']}", **stat)
        # (the healthy baseline is folded exactly once, in the OBSERVE/DONE branch below, to avoid
        # double-counting the same heal's sample)

    # --- DECISION: ADK SequentialAgent (Gemini calls the tools) -> direct Gemini -> heuristic --
    decision = adk_brain.decide(service)
    if decision:
        emit("ADK", f"ADK SequentialAgent (triage→decide) ran; "
                    f"tools called: {decision.get('_adk_tools') or '—'}")
    else:
        decision = gemini.decide(service, revs, err) or _heuristic(revs, err)
    decision = _validate(decision, revs, stat)
    if decision.get("action") == "ROLLBACK" and not decision.get("bad_revision"):
        # backfill the bad (currently-serving) revision when the LLM left it null, so a later
        # complete_rollback can never auto-pick the known-bad revision as the "fix" (mirrors _heuristic).
        serving = max(revs.get("revisions", []), key=lambda r: r.get("traffic_percent", 0), default=None)
        if serving:
            decision["bad_revision"] = serving["name"]
    emit("DECISION", decision["action"], **decision)
    _decision_summary = {k: decision.get(k) for k in (
        "action", "confidence", "reasoning", "evidence", "_source", "_adk_tools",
        "bad_revision", "rollback_revision")}
    if decision["action"] != "ROLLBACK":
        # ESCALATE (from the safety gate or Gemini) must surface to a human — not look like a no-op.
        if decision["action"] == "ESCALATE":
            emit("ESCALATED", decision.get("reasoning") or "decision gate failed — needs a human")
            incidents.record(incident_id, {"service": service, "status": "escalated",
                                           "decision": _decision_summary, "events": run_events})
            return {"status": "escalated", "incident_id": incident_id, "events": run_events}
        emit("DONE", "no rollback needed")
        # OBSERVE = the service is healthy at real traffic -> a genuine steady-state baseline sample
        memory.observe_healthy(service, stat["rate"] if stat else (before.get("error_rate") or 0.0))
        incidents.record(incident_id, {"service": service, "status": "noop",
                                       "decision": _decision_summary, "events": run_events})
        return {"status": "noop", "incident_id": incident_id, "events": run_events}

    # --- CROSS-INCIDENT MEMORY: is this failure recurring? (advisory — doesn't change the action) --
    signature = _incident_signature()
    recur = memory.recurrence(service, signature)
    if recur >= config.RECUR_THRESHOLD:
        emit("RECURRING", f"{recur} similar incidents on {service} within the window — the fix "
                          f"may not be holding; a human should look", count=recur)

    # --- AUTONOMY GATE: how much may Airbag do on its own for THIS service? ---------------
    level = autonomy.level_for(service)
    target = decision["rollback_revision"]
    if level == "L0":  # observe-only: decide + report, never touch prod
        emit("OBSERVE_ONLY", f"autonomy L0 — would roll back to {target}, but acting is disabled",
             rollback_revision=target)
        memory.record_incident(service, signature, "observed")
        incidents.record(incident_id, {"service": service, "status": "observed", "autonomy": level,
                                       "decision": _decision_summary, "events": run_events})
        return {"status": "observed", "incident_id": incident_id, "events": run_events}
    if level == "L1":  # gate BEFORE the rollback — wait for a human to approve touching prod
        autonomy.save_approval(incident_id, {"service": service, "kind": "rollback",
                                             "decision": decision, "before": before, "target": target})
        emit("AWAITING_APPROVAL", f"autonomy L1 — rollback to {target} needs operator approval",
             kind="rollback", rollback_revision=target)
        # NB: don't record_incident here — the gate isn't a terminal outcome; the single memory
        # record happens when the heal actually resolves (mitigated on approve, or denied).
        incidents.record(incident_id, {"service": service, "status": "awaiting_approval",
                                       "autonomy": level, "decision": _decision_summary,
                                       "rollback_revision": target, "events": run_events})
        return {"status": "awaiting_approval", "incident_id": incident_id, "events": run_events}

    # L2 / L3 — auto-rollback now (stop the bleeding; reversible). L2 then gates the forward fix-PR.
    return _mitigate(service, incident_id, decision, _decision_summary, before, target,
                     emit, run_events, gate_fix_pr=(level == "L2"), level=level)


def _mitigate(service: str, incident_id: str, decision: dict, decision_summary: dict, before: dict,
              target: str, emit, run_events: list, *, gate_fix_pr: bool, level: str) -> dict:
    """Apply the rollback, prove recovery, then either open the fix-PR (L3 / approved L1) or gate it
    for approval (L2). Shared by run_self_heal and apply_approval so the L1 resume replays it."""
    result = tools.rollback_traffic_to_revision(service, config.GCP_REGION, target)
    rollback_at = time.time()
    emit("ROLLBACK_APPLIED", f"100% traffic -> {target}", result=result)

    if not _verify(service, emit, since_epoch=rollback_at):
        autonomy.record_outcome(service, success=False)  # fail-safe: a bad heal demotes autonomy
        # the rollback DID shift traffic; track it so a later fix can still complete (or undo) it
        # instead of stranding the routing (complete_rollback re-verifies health before acting).
        pending.set_pending(service, {
            "incident_id": incident_id, "bad_revision": decision.get("bad_revision"),
            "rolled_back_to": target, "rollback_at_epoch": rollback_at, "pr_url": None})
        emit("ESCALATED", "rollback did not clear errors within budget — held for a fix / manual revert")
        memory.record_incident(service, _incident_signature(), "escalated", target)
        incidents.record(incident_id, {"service": service, "status": "escalated", "autonomy": level,
                                       "decision": decision_summary, "rolled_back_to": target,
                                       "error_before": before.get("error_rate"), "events": run_events})
        return {"status": "escalated", "incident_id": incident_id, "events": run_events}

    after = tools.query_error_rate(service, config.GCP_REGION, window_minutes=2, since_epoch=rollback_at)
    note = gemini.explain_recovery(service, before, after)
    emit("MITIGATED", note or "error rate back to zero — recovery proven",
         before=before.get("error_rate"), after=after.get("error_rate"))
    autonomy.record_outcome(service, success=True)  # trust ramp: a verified heal builds the streak

    ctx = (f"bad revision {decision.get('bad_revision')} on {service} returned HTTP 500 on the "
           f"business path {config.PROBE_PATH} (unhandled exception, not an explicit error "
           f"response); evidence: {decision.get('evidence')}")

    if gate_fix_pr:  # L2: the rollback is applied + held, but the forward fix-PR waits for approval
        autonomy.save_approval(incident_id, {"service": service, "kind": "fix_pr", "ctx": ctx,
                                             "target": target, "rollback_at_epoch": rollback_at})
        _arm_pending(service, incident_id, decision, target, rollback_at, None, emit)
        emit("AWAITING_APPROVAL", "autonomy L2 — fix PR needs approval before it's opened", kind="fix_pr")
        memory.record_incident(service, _incident_signature(), "awaiting_fix_approval", target)
        incidents.record(incident_id, {
            "service": service, "status": "awaiting_fix_approval", "autonomy": level,
            "decision": decision_summary, "rolled_back_to": target,
            "error_before": before.get("error_rate"), "error_after": after.get("error_rate"),
            "events": run_events})
        return {"status": "awaiting_fix_approval", "incident_id": incident_id,
                "rolled_back_to": target, "events": run_events}

    pr_url = _open_fix_pr(service, incident_id, ctx, emit)
    _arm_pending(service, incident_id, decision, target, rollback_at, pr_url, emit)
    memory.record_incident(service, _incident_signature(), "mitigated", target)
    incidents.record(incident_id, {
        "service": service, "status": "mitigated", "autonomy": level, "decision": decision_summary,
        "rolled_back_to": target, "error_before": before.get("error_rate"),
        "error_after": after.get("error_rate"), "pr_url": pr_url, "events": run_events})
    return {"status": "mitigated", "incident_id": incident_id, "rolled_back_to": target,
            "events": run_events}


def _open_fix_pr(service: str, incident_id: str, ctx: str, emit) -> str | None:
    """Gemini opens a real fix PR through CI; on red CI it self-corrects in the background."""
    from . import github_pr
    if not github_pr.available():
        emit("FIX_PR", "fix-PR slow path not configured (set GITHUB_TOKEN/GITHUB_REPO)")
        return None
    pr = github_pr.open_fix_pr(service, ctx)
    if not pr:
        emit("FIX_PR", "no fix PR opened (no change or error)")
        return None
    pr_url = pr["pr_url"]
    emit("FIX_PR", f"opened fix PR — {pr['summary']}", pr_url=pr_url)
    if config.CI_SELF_CORRECT and pr.get("number"):
        import threading

        def _watch_emit(stage: str, msg: str, **data):
            ev = events.publish({"incident_id": incident_id, "service": service,
                                 "stage": stage, "msg": msg, **data})
            incidents.record(incident_id, {"events": [ev]})

        threading.Thread(target=github_pr.self_correct_ci,
                         args=(pr["branch"], pr["number"], service, ctx, _watch_emit),
                         daemon=True).start()
        emit("CI_WATCH", "watching the fix PR's CI — will self-correct on red")
    return pr_url


def _arm_pending(service: str, incident_id: str, decision: dict, target: str,
                 rollback_at: float, pr_url: str | None, emit) -> None:
    """Remember the temporary rollback so it can be UNDONE once the fix deploys + verifies
    (the fix-PR's CI calls /internal/complete-rollback; or the dashboard's Verify & Undo)."""
    pending.set_pending(service, {
        "incident_id": incident_id, "bad_revision": decision.get("bad_revision"),
        "rolled_back_to": target, "rollback_at_epoch": rollback_at, "pr_url": pr_url})
    emit("PENDING_REVERT", "rollback held until the fix deploys + is verified",
         rolled_back_to=target, pr_url=pr_url)


def apply_approval(incident_id: str, approve: bool) -> dict:
    """Resume an L1 rollback or L2 fix-PR that was gated for a human decision. Durable: the approval
    was persisted in the store, so this works even after the deciding instance was recycled."""
    appr = autonomy.claim_approval(incident_id)  # atomic read+delete: a double-click resumes once
    if appr is None:
        return {"status": "noop", "reason": "no pending approval (or it expired)",
                "incident_id": incident_id}
    service, kind = appr["service"], appr.get("kind")
    run_events: list[dict] = []

    def emit(stage: str, msg: str, **data):
        log.info("[%s] %s %s", stage, msg, data or "")
        ev = events.publish({"incident_id": incident_id, "service": service,
                             "stage": stage, "msg": msg, **data})
        run_events.append(ev)

    if not approve:
        emit("DENIED", f"{kind} denied by operator")
        memory.record_incident(service, _incident_signature(), f"{kind}_denied")
        incidents.record(incident_id, {"service": service, "status": f"{kind}_denied",
                                       "events": run_events})
        return {"status": "denied", "kind": kind, "incident_id": incident_id, "events": run_events}

    emit("APPROVED", f"{kind} approved by operator")
    if kind == "rollback":  # L1: re-validate the (up to APPROVAL_TTL_S-old) decision before touching prod
        decision = appr.get("decision", {})
        before = appr.get("before") or {"error_rate": None}
        target = appr["target"]
        revs = tools.list_cloud_run_revisions(service, config.GCP_REGION)
        if target not in {r["name"] for r in revs.get("revisions", []) if r.get("ready")}:
            emit("ESCALATED", f"approval stale — rollback target {target} is no longer ready; not acting")
            incidents.record(incident_id, {"service": service, "status": "escalated", "events": run_events})
            return {"status": "escalated", "incident_id": incident_id, "events": run_events}
        if config.STAT_GATE_ENABLED:  # don't roll back a service that already self-recovered
            s = tools.sample_business_path(service, config.GCP_REGION, config.STAT_SAMPLE_N)
            v = analyzer.analyze(s["errs"], s["total"], memory.baseline_for(service),
                                 z=config.STAT_Z, min_fail_errors=config.STAT_MIN_FAIL_ERRORS)
            emit("ANALYZED", f"re-check at approval: {v['verdict']} — {v['reason']}", **v)
            if v["verdict"] == "PASS":
                emit("DONE", "service already healthy at approval time — rollback no longer needed")
                memory.observe_healthy(service, v["rate"])
                incidents.record(incident_id, {"service": service, "status": "noop", "events": run_events})
                return {"status": "noop", "incident_id": incident_id, "events": run_events}
        return _mitigate(service, incident_id, decision, decision, before, target,
                         emit, run_events, gate_fix_pr=False, level="L1")
    if kind == "fix_pr":  # L2: rollback already applied + held; now open the approved fix-PR
        pr_url = _open_fix_pr(service, incident_id, appr.get("ctx", ""), emit)
        pend = pending.get_pending(service)
        if pend:
            pending.set_pending(service, {**pend, "pr_url": pr_url})
        incidents.record(incident_id, {"service": service, "status": "mitigated",
                                       "pr_url": pr_url, "events": run_events})
        return {"status": "mitigated", "kind": "fix_pr", "pr_url": pr_url,
                "incident_id": incident_id, "events": run_events}
    return {"status": "noop", "reason": f"unknown approval kind {kind}", "incident_id": incident_id}


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

    def _save(status: str, **extra) -> None:
        incidents.record(incident_id, {"service": service, "status": status,
                                       "events": run_events, **extra})

    closed = False
    # Cap compensation retries: after MAX failed undos, stop (traffic is already on the safe
    # revision) and require a human — don't keep re-shifting traffic on every re-trigger.
    if rec.get("attempts", 0) >= config.MAX_UNDO_ATTEMPTS:
        emit("MANUAL_INTERVENTION",
             f"giving up after {rec.get('attempts')} failed undo attempts — traffic stays on the "
             f"safe revision {rec.get('rolled_back_to')}; needs a human")
        pending.clear_pending(service)  # terminal: no further auto-undo
        _save("manual_intervention")
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
            _save("manual_intervention")
            return {"status": "manual_intervention", "reason": "no candidate fix revision",
                    "incident_id": incident_id, "events": run_events}
        emit("FIX_DEPLOYED", f"candidate fix revision: {candidate}",
             revision=candidate, git_sha=git_sha, pr_url=pr_url or rec.get("pr_url"))

        # Gradual canary: shift traffic to the fix in stages, gating on health at each step;
        # compensate to the safe revision on any gate failure (catch a bad fix at low exposure).
        safe = rec.get("rolled_back_to")
        for pct in config.CANARY_STAGES:
            split = {candidate: 100} if pct >= 100 else {candidate: pct, safe: 100 - pct}
            # tag the candidate so we can probe IT directly (not the load-balanced URL, which at
            # 10% would almost always hit the healthy revision and miss a bad fix).
            tools.set_traffic_split(service, config.GCP_REGION, split, tag_revision=candidate)
            stage_at = time.time()
            cand = tools.probe_candidate(service, config.GCP_REGION, candidate)
            emit("CANARY", f"{pct}% → fix {candidate}"
                 + ("" if pct >= 100 else f" · {100 - pct}% → {safe}")
                 + f"; direct fix-probe ok={cand.get('ok')} "
                   f"({cand.get('errors')}/{cand.get('total')} 5xx)",
                 percent=pct, candidate_ok=cand.get("ok"))
            # gate = the fix's OWN health (direct probe) AND the service-level recovery signal
            if not (cand.get("ok") and _verify(service, emit, since_epoch=stage_at)):
                tools.set_traffic_split(service, config.GCP_REGION, {safe: 100})  # compensate
                attempts = pending.bump_attempts(service)
                autonomy.record_outcome(service, success=False)  # a bad fix caught at canary demotes trust
                emit("MANUAL_INTERVENTION",
                     f"fix {candidate} failed the {pct}% canary gate (attempt {attempts}/"
                     f"{config.MAX_UNDO_ATTEMPTS}) — compensated: 100% traffic back on {safe}")
                _save("compensated", safe_revision=safe, attempts=attempts, canary_failed_at=pct)
                return {"status": "compensated", "safe_revision": safe, "attempts": attempts,
                        "incident_id": incident_id, "events": run_events}
        emit("ROLLBACK_UNDONE",
             f"temporary rollback undone — traffic fully restored to the fix ({candidate}) "
             f"via canary {config.CANARY_STAGES}")
        emit("CLOSED", "incident closed: rolled back, fixed, and traffic restored to the fix")
        closed = True
        _save("closed", restored_to=candidate, fix_git_sha=git_sha)
        return {"status": "closed", "restored_to": candidate,
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


def _validate(decision: dict, revs: dict, stat: dict | None = None) -> dict:
    """Safety gate: only roll back to a known-good revision above the confidence threshold AND
    when the statistical analyzer confidently confirms the degradation. The gate is a CONSTRAINT
    on ROLLBACK — it never forces action (so an INCONCLUSIVE signal with a Gemini OBSERVE stays a
    quiet OBSERVE, not an alarm)."""
    if decision.get("action") != "ROLLBACK":
        return decision
    # Statistical gate FIRST (per review): a statistically-healthy service must OBSERVE even if the
    # LLM hallucinated a bad rollback target — don't escalate a healthy service over a bad target.
    # PASS -> don't roll back; INCONCLUSIVE -> human; FAIL -> fall through to the confidence/target check.
    if stat is not None:
        v = stat.get("verdict")
        if v == "PASS":
            return {**decision, "action": "OBSERVE",
                    "reasoning": f"statistical gate PASS — {stat.get('reason')}; rollback withheld. "
                                 f"{decision.get('reasoning', '')}"}
        if v == "INCONCLUSIVE":
            return {**decision, "action": "ESCALATE",
                    "reasoning": f"statistical gate INCONCLUSIVE — {stat.get('reason')}; insufficient "
                                 f"evidence to auto-roll-back. {decision.get('reasoning', '')}"}
    # then the confidence + known-good-target gate (reached only when stat is FAIL or absent)
    revs = revs or {}
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
