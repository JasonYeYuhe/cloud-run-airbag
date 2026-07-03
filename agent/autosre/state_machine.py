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

from . import (adk_brain, autonomy, causal, config, events, gemini, incidents, memory,
               pending, reversibility, signals, state_store, tools)

log = logging.getLogger("airbag.sm")


def _incident_signature() -> str:
    """A coarse, stable failure fingerprint for recurrence detection across incidents."""
    return f"5xx:{config.PROBE_PATH}"


# v5 Phase 1.1: which settled outcomes are ELIGIBLE to keep the per-service correlation lease live.
# The spec (V5_VISION §3 1.1) holds the lease only while the outcome is unsettled WITH A LIVE
# approval/pending a late re-fire can coalesce onto — NOT on the bare status. So this set is just the
# statuses that CAN hold; _service_hold_seconds gates the real hold on a live approval/pending
# actually existing. "escalated" is here because the verify-FAILURE escalate arms a pending (a re-fire
# must coalesce, not re-roll-back a still-broken service); but a BARE escalate (decision-gate
# ESCALATE, reversibility BLOCK, causal COINCIDENT, stale target) armed neither and must RELEASE — it
# is a corpse a follower must never attach to. Everything else (mitigated/noop settled-good,
# manual_intervention/exhausted terminal-failed) releases so the next alert becomes a FRESH leader.
_MAYBE_HELD_STATES = frozenset({"awaiting_approval", "awaiting_fix_approval", "escalated"})


def _service_hold_seconds(status: str, incident_id: str, service: str) -> float:
    """How long the correlation lease stays live after a settle. Holds (the approval window) ONLY when
    the outcome is eligible AND a live approval/pending actually exists to coalesce onto: the L1/L2
    gates (a saved approval) or the verify-FAILURE escalate (an armed pending revert). A bare escalate
    armed neither, so it RELEASES (hold 0). mitigated arms a pending too but is settled-GOOD (not
    eligible) — the service is healthy, so a new alert is a NEW outage that must heal, not coalesce."""
    if status in _MAYBE_HELD_STATES and (autonomy.get_approval(incident_id)
                                         or pending.get_pending(service)):
        return config.APPROVAL_TTL_S
    return 0.0


def _attach_to_leader(incident_id: str, service: str, leader_incident_id: str | None) -> dict:
    """A storm FOLLOWER: a live leader is already healing this service, so this alert (a distinct
    Monitoring incident id for the SAME outage) coalesces onto it instead of running a second heal.
    Returns BEFORE triage — the whole point of 1.1 is that a follower emits NO diagnostic probes (the
    self-amplification that fired the very alert being diagnosed on 2026-07-02)."""
    state_store.finish_heal(incident_id, config.DEDUP_TTL_S)  # this follower id is terminal -> its redelivery no-ops
    ev = events.publish({"incident_id": incident_id, "service": service, "stage": "ATTACHED",
                         "leader_incident_id": leader_incident_id,
                         "msg": f"coalesced onto the in-flight heal {leader_incident_id} for {service} "
                                f"(storm follower — no second heal, no diagnostic probes)"})
    incidents.record(incident_id, {"service": service, "status": "attached",
                                   "leader_incident_id": leader_incident_id, "events": [ev]})
    log.info("[ATTACHED] %s coalesced onto leader %s for %s", incident_id, leader_incident_id, service)
    return {"status": "attached", "incident_id": incident_id,
            "leader_incident_id": leader_incident_id, "events": [ev]}


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
        # v5 Phase 1.1: a terminally-failed leader RELEASES the per-service lease (hold 0) so the next
        # alert for the still-broken service becomes a FRESH leader — never attaches to a corpse (§3 1.1).
        # No-op unless this incident is still the current leader (it is: the prior failing attempts
        # claimed the lease as leader), so it can't clobber a lease already taken over.
        if config.STORM_COALESCE:
            state_store.settle_service_heal(service, incident_id, "manual_intervention", 0.0)
        return {"status": "manual_intervention", "incident_id": incident_id,
                "reason": f"heal failed {config.MAX_HEAL_ATTEMPTS}x"}
    # v5 Phase 1.1: per-service correlation lease — an alert STORM (N distinct incident ids for ONE
    # broken service) coalesces onto a single leader; followers ATTACH and return before triage.
    if config.STORM_COALESCE:
        role, leader = state_store.claim_service_heal(service, incident_id,
                                                      config.SERVICE_HEAL_LEASE_S)
        if role == "follower":
            return _attach_to_leader(incident_id, service, leader)
    try:
        result = _heal_body(incident_id, service)
    except Exception:
        # transient failure -> let a retry re-claim (attempts bumped). The service lease stays HELD
        # (outcome still 'running'); the SAME incident's redelivery resumes as leader (never a
        # follower attaching to itself), and a true crash is caught by the lease's TTL backstop.
        state_store.release_heal(incident_id)
        raise
    if config.STORM_COALESCE:
        status = result.get("status", "unknown")
        state_store.settle_service_heal(service, incident_id, status,
                                        _service_hold_seconds(status, incident_id, service))
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
    # v4: the serving-history ledger (witnessed-healthy revisions) informs the DETERMINISTIC target
    # selectors — the heuristic and _validate's promotion. It only PROPOSES: whatever target is
    # chosen still flows through the live causal pre-check in _mitigate before any traffic shifts.
    witnessed = memory.witnessed_healthy(service)
    decision = adk_brain.decide(service)
    if decision:
        emit("ADK", f"ADK SequentialAgent (triage→decide) ran; "
                    f"tools called: {decision.get('_adk_tools') or '—'}")
    else:
        decision = gemini.decide(service, revs, err) or _heuristic(revs, err, witnessed)
    decision = _validate(decision, revs, stat, witnessed)
    if decision.get("action") == "ROLLBACK" and not decision.get("bad_revision"):
        # backfill the bad (currently-serving) revision when the LLM left it null, so a later
        # complete_rollback can never auto-pick the known-bad revision as the "fix" (mirrors _heuristic).
        serving = max(revs.get("revisions", []), key=lambda r: r.get("traffic_percent", 0), default=None)
        if serving:
            decision["bad_revision"] = serving["name"]
    emit("DECISION", decision["action"], **decision)
    _decision_summary = {k: decision.get(k) for k in (
        "action", "confidence", "reasoning", "evidence", "_source", "_adk_tools",
        "bad_revision", "rollback_revision", "_target_source", "_target_overridden")}
    if decision["action"] != "ROLLBACK":
        # ESCALATE (from the safety gate or Gemini) must surface to a human — not look like a no-op.
        if decision["action"] == "ESCALATE":
            emit("ESCALATED", decision.get("reasoning") or "decision gate failed — needs a human")
            incidents.record(incident_id, {"service": service, "status": "escalated",
                                           "decision": _decision_summary, "events": run_events})
            return {"status": "escalated", "incident_id": incident_id, "events": run_events}
        emit("DONE", "no rollback needed")
        # OBSERVE = the service is healthy at real traffic -> a genuine steady-state baseline sample.
        # observe_healthy is a 5xx statistic: fold the 5xx rate (stat carries it only when the 5xx
        # detector ran); if 5xx isn't in the signal mix, don't fabricate a 0.0 into the 5xx EMA.
        _rate = stat.get("rate") if stat else (before.get("error_rate") or 0.0)
        if _rate is not None:
            memory.observe_healthy(service, _rate)
        # v4 serving-history ledger: a CONFIDENTLY-healthy no-op run witnesses the revision that was
        # serving the traffic (PASS, or zero observed 5xx) — the fact the rollback selector prefers.
        if _healthy_witness(stat, before):
            witnessed = _serving_revision(revs)
            if witnessed:
                memory.witness_serving(service, witnessed)
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
                     emit, run_events, gate_fix_pr=(level == "L2"), level=level,
                     primary_signal=_primary_signal(stat))


def _mitigate(service: str, incident_id: str, decision: dict, decision_summary: dict, before: dict,
              target: str, emit, run_events: list, *, gate_fix_pr: bool, level: str,
              primary_signal: str = "5xx") -> dict:
    """Apply the rollback, prove recovery, then either open the fix-PR (L3 / approved L1) or gate it
    for approval (L2). Shared by run_self_heal and apply_approval so the L1 resume replays it.
    `primary_signal` is the detector that drove the incident — a latency regression's remedy is the
    rollback itself, so we don't open a code-fix PR (that path targets 5xx/code-bug incidents)."""
    causal_verdict: dict | None = None
    # REVERSIBILITY GUARD (v4 Phase 3, default-OFF, fail-open): if the rollback would CROSS a
    # revision that DECLARED a forward-only change (schema migration → annotation
    # airbag.dev/irreversible=true), the "reversible" action isn't — old code in front of a
    # migrated datastore makes the outage strictly worse. HONORS the declared contract only (does
    # not detect migrations); LLM-free + deterministic; runs before the causal probe (no point
    # probing a target we must refuse). Covers L2/L3 auto AND the L1-approved resume.
    if config.REVERSIBILITY_GUARD_ENABLED:
        rev_check = reversibility.check(tools.list_cloud_run_revisions(service, config.GCP_REGION),
                                        target)
        emit("REVERSIBILITY", f"{rev_check['verdict']} — {rev_check['reason']}",
             **{k: rev_check[k] for k in ("verdict", "marker_revision", "target", "marker_value")
                if k in rev_check})
        if rev_check["verdict"] == "BLOCK":
            memory.record_incident(service, _incident_signature(), "escalated", target)
            emit("ESCALATED", rev_check["reason"])
            incidents.record(incident_id, {"service": service, "status": "escalated",
                                           "autonomy": level, "decision": decision_summary,
                                           "reversibility": rev_check,
                                           "error_before": before.get("error_rate"),
                                           "events": run_events})
            return {"status": "escalated", "incident_id": incident_id, "events": run_events}
    # CAUSAL PRE-CHECK (v3 Phase 2a): before spending the reversible action, probe the rollback
    # TARGET's health. If the last-good revision is ALSO confidently degraded, the cause is external
    # (a dependency/quota outage), not this revision — a rollback is futile. Only a CONFIDENT-unhealthy
    # target escalates; a transient/ambiguous/errored probe proceeds (never blocks a legit rollback).
    # Sits at the TOP of _mitigate so it covers L2/L3 auto AND the L1-approved resume, and only runs
    # once a rollback is actually imminent (never before the L0/L1 gate). Default off (demo unchanged).
    if config.CAUSAL_CHECK_ENABLED:
        # the probe is keyed on the TRIGGERING signal (v4 Phase 2): a latency incident also vetoes
        # a confidently-SLOW target — a 200-but-slow target can't remedy a latency regression.
        c = causal.precheck(service, config.GCP_REGION, target, primary_signal=primary_signal)
        causal_verdict = c
        emit("CAUSAL", f"{c['verdict']} — {c['reason']}", **{k: c[k] for k in ("verdict", "target") if k in c})
        if c["verdict"] == "COINCIDENT":
            memory.record_incident(service, _incident_signature(), "escalated", target)
            emit("ESCALATED", c["reason"])
            incidents.record(incident_id, {"service": service, "status": "escalated", "autonomy": level,
                                           "decision": decision_summary, "causal": c,
                                           "error_before": before.get("error_rate"), "events": run_events})
            return {"status": "escalated", "incident_id": incident_id, "events": run_events}
    result = tools.rollback_traffic_to_revision(service, config.GCP_REGION, target)
    rollback_at = time.time()
    emit("ROLLBACK_APPLIED", f"100% traffic -> {target}", result=result)

    if not _verify(service, emit, since_epoch=rollback_at, primary_signal=primary_signal):
        autonomy.record_outcome(service, success=False, incident_id=incident_id)  # fail-safe: a bad heal demotes autonomy
        # the rollback DID shift traffic; track it so a later fix can still complete (or undo) it
        # instead of stranding the routing (complete_rollback re-verifies health before acting).
        pending.set_pending(service, {
            "incident_id": incident_id, "bad_revision": decision.get("bad_revision"),
            "rolled_back_to": target, "rollback_at_epoch": rollback_at, "pr_url": None})
        emit("ESCALATED", "rollback did not clear errors within budget — held for a fix / manual revert")
        memory.record_incident(service, _incident_signature(), "escalated", target)
        incidents.record(incident_id, {"service": service, "status": "escalated", "autonomy": level,
                                       "decision": decision_summary, "rolled_back_to": target,
                                       "error_before": before.get("error_rate"),
                                       **({"causal": causal_verdict} if causal_verdict else {}),
                                       "events": run_events})
        return {"status": "escalated", "incident_id": incident_id, "events": run_events}

    after = tools.query_error_rate(service, config.GCP_REGION, window_minutes=2, since_epoch=rollback_at)
    note = gemini.explain_recovery(service, before, after)
    emit("MITIGATED", note or "error rate back to zero — recovery proven",
         before=before.get("error_rate"), after=after.get("error_rate"))
    autonomy.record_outcome(service, success=True, incident_id=incident_id)  # trust ramp: a verified heal builds the streak
    # v4 serving-history ledger: _verify PROVED the target recovered the triggering signal at 100%
    # traffic — the strongest witness Airbag collects. (An unverified shift — the escalate branch
    # above — must never stamp: traffic moving is not evidence, recovery proof is.) Defense in
    # depth (Gemini review): also require the post-rollback 5xx window to read clean — a lagged
    # log window plus a flaky-but-lucky probe must not certify a still-erring target.
    if after.get("error_rate", 0.0) == 0.0:
        memory.witness_serving(service, target)

    # shared record fields (causal verdict included when the pre-check ran, so the proof bundle is complete)
    rec = {"service": service, "autonomy": level, "decision": decision_summary,
           "rolled_back_to": target, "error_before": before.get("error_rate"),
           "error_after": after.get("error_rate"), "events": run_events}
    if causal_verdict is not None:
        rec["causal"] = causal_verdict

    # A LATENCY regression's remedy IS the rollback to the healthy revision — there's no HTTP 500 / code
    # bug for a forward fix-PR to repair, so we don't fabricate one (that path targets 5xx/code-bug
    # incidents like the KeyError). We STILL arm the pending-revert: the rollback pinned traffic to an
    # explicit revision, so the pin must be tracked (a later healthy deploy would otherwise get 0%
    # traffic until complete_rollback / the dashboard's Verify & Undo restores it).
    if primary_signal != "5xx":
        emit("FIX_PR", f"no forward code-fix PR — a {primary_signal} regression is remedied by the "
                       f"rollback to the healthy revision (the fix-PR path targets 5xx/code-bug incidents)")
        _arm_pending(service, incident_id, decision, target, rollback_at, None, emit,
                     note="traffic pinned to the healthy revision; restored to a newer healthy "
                          "revision when one deploys + verifies (or via Verify & Undo)")
        memory.record_incident(service, _incident_signature(), "mitigated", target)
        incidents.record(incident_id, {**rec, "status": "mitigated", "pr_url": None})
        return {"status": "mitigated", "incident_id": incident_id, "rolled_back_to": target,
                "events": run_events}

    ctx = (f"bad revision {decision.get('bad_revision')} on {service} returned HTTP 500 on the "
           f"business path {config.PROBE_PATH} (unhandled exception, not an explicit error "
           f"response); evidence: {decision.get('evidence')}")

    if gate_fix_pr:  # L2: the rollback is applied + held, but the forward fix-PR waits for approval
        autonomy.save_approval(incident_id, {"service": service, "kind": "fix_pr", "ctx": ctx,
                                             "target": target, "rollback_at_epoch": rollback_at})
        _arm_pending(service, incident_id, decision, target, rollback_at, None, emit)
        emit("AWAITING_APPROVAL", "autonomy L2 — fix PR needs approval before it's opened", kind="fix_pr")
        memory.record_incident(service, _incident_signature(), "awaiting_fix_approval", target)
        incidents.record(incident_id, {**rec, "status": "awaiting_fix_approval"})
        return {"status": "awaiting_fix_approval", "incident_id": incident_id,
                "rolled_back_to": target, "events": run_events}

    pr_url = _open_fix_pr(service, incident_id, ctx, emit)
    _arm_pending(service, incident_id, decision, target, rollback_at, pr_url, emit)
    memory.record_incident(service, _incident_signature(), "mitigated", target)
    incidents.record(incident_id, {**rec, "status": "mitigated", "pr_url": pr_url})
    return {"status": "mitigated", "incident_id": incident_id, "rolled_back_to": target,
            "events": run_events}


def _open_fix_pr(service: str, incident_id: str, ctx: str, emit) -> str | None:
    """Gemini opens a real fix PR through CI; on red CI it self-corrects in the background."""
    from . import github_pr
    if not github_pr.available():
        emit("FIX_PR", "fix-PR slow path not configured (set GITHUB_TOKEN/GITHUB_REPO)")
        return None
    # v5 4.1: pass the incident signature so PR reuse is keyed on the incident CLASS (not any fix branch)
    pr = github_pr.open_fix_pr(service, ctx, signature=_incident_signature())
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

        # v5 4.1: thread the DISCOVERED fix path so CI self-correction repairs the file the pipeline
        # actually wrote (corrections used to hardcode config.FIX_FILE and could never fix it elsewhere).
        threading.Thread(target=github_pr.self_correct_ci,
                         args=(pr["branch"], pr["number"], service, ctx, _watch_emit),
                         kwargs={"path": pr.get("path")}, daemon=True).start()
        emit("CI_WATCH", "watching the fix PR's CI — will self-correct on red")
    return pr_url


def _arm_pending(service: str, incident_id: str, decision: dict, target: str,
                 rollback_at: float, pr_url: str | None, emit, note: str | None = None) -> None:
    """Remember the temporary rollback so it can be UNDONE once the fix deploys + verifies
    (the fix-PR's CI calls /internal/complete-rollback; or the dashboard's Verify & Undo). Tracking
    the pin is REQUIRED even without a fix-PR: rollback pins traffic to an explicit revision, so a
    later healthy deploy would get 0% traffic until complete_rollback restores it."""
    pending.set_pending(service, {
        "incident_id": incident_id, "bad_revision": decision.get("bad_revision"),
        "rolled_back_to": target, "rollback_at_epoch": rollback_at, "pr_url": pr_url})
    emit("PENDING_REVERT", note or "rollback held until the fix deploys + is verified",
         rolled_back_to=target, pr_url=pr_url)


def apply_approval(incident_id: str, approve: bool) -> dict:
    """Resume a gated L1/L2 decision, then (v5 Phase 1.1) settle the per-service correlation lease to
    MATCH the human decision — release a held storm-lease on mitigated/denied, re-hold it if the
    resumed heal re-escalates — so late re-fires coalesce or a fresh outage claims a fresh leader.
    Flag-off (default): this is byte-identical to the pre-v5 body (no lease peek, no settle)."""
    service = None
    if config.STORM_COALESCE:  # peek the service (read-only) BEFORE the body atomically claims+deletes it
        service = (autonomy.get_approval(incident_id) or {}).get("service")
    result = _apply_approval_body(incident_id, approve)
    if config.STORM_COALESCE and service:
        # the approval was CLAIMED (deleted) inside the body, so a re-hold now keys on the live PENDING
        # a resumed heal armed (verify-fail escalate) — a dead-end re-escalate has none, so it releases.
        status = result.get("status", "unknown")
        state_store.settle_service_heal(service, incident_id, status,
                                        _service_hold_seconds(status, incident_id, service))
    return result


def _apply_approval_body(incident_id: str, approve: bool) -> dict:
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
        primary_signal = "5xx"  # v2 default when the stat gate is off
        if config.STAT_GATE_ENABLED:  # don't roll back a service that already self-recovered
            # re-check through the SAME multi-signal engine as triage (not 5xx-only) so a latency/
            # etc. rollback isn't abandoned on a 5xx-blind PASS. PASS = all enabled detectors healthy.
            v = signals.detect(service, config.GCP_REGION, memory.baseline_for(service))
            emit("ANALYZED", f"re-check at approval: {v['verdict']} — {v['reason']}", **v)
            if v["verdict"] == "PASS":
                emit("DONE", "service already healthy at approval time — rollback no longer needed")
                if "rate" in v:  # observe_healthy is a 5xx statistic — only fold when 5xx ran
                    memory.observe_healthy(service, v["rate"])
                witnessed = _serving_revision(revs)   # v4 ledger: a PASS at real traffic witnesses
                if witnessed:                         # the revision that was serving it
                    memory.witness_serving(service, witnessed)
                incidents.record(incident_id, {"service": service, "status": "noop", "events": run_events})
                return {"status": "noop", "incident_id": incident_id, "events": run_events}
            # carry the triggering signal into _mitigate so the L1 resume verifies + remediates the
            # RIGHT signal (a latency incident shouldn't verify 5xx-only or open a bogus HTTP-500 PR).
            primary_signal = _primary_signal(v)
        return _mitigate(service, incident_id, decision, decision, before, target,
                         emit, run_events, gate_fix_pr=False, level="L1", primary_signal=primary_signal)
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

    def _release_service_lease(status: str) -> None:
        # v5 Phase 1.1: a TERMINAL completion (closed / manual_intervention) releases the per-service
        # correlation lease so a genuinely-new outage becomes a fresh leader instead of coalescing onto
        # this (now-resolved) incident. `compensated` is NOT terminal (pending kept, retry possible) —
        # it leaves the lease held. No-op unless this incident is still the current leader.
        if config.STORM_COALESCE:
            state_store.settle_service_heal(service, incident_id, status, 0.0)

    closed = False
    # Cap compensation retries: after MAX failed undos, stop (traffic is already on the safe
    # revision) and require a human — don't keep re-shifting traffic on every re-trigger.
    if rec.get("attempts", 0) >= config.MAX_UNDO_ATTEMPTS:
        emit("MANUAL_INTERVENTION",
             f"giving up after {rec.get('attempts')} failed undo attempts — traffic stays on the "
             f"safe revision {rec.get('rolled_back_to')}; needs a human")
        pending.clear_pending(service)  # terminal: no further auto-undo
        _save("manual_intervention")
        _release_service_lease("manual_intervention")
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
            _release_service_lease("manual_intervention")
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
                autonomy.record_outcome(service, success=False, incident_id=incident_id)  # a bad fix caught at canary demotes trust
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
        _release_service_lease("closed")
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


def _preferred_target(candidates: list[dict], witnessed: dict | None) -> tuple[dict | None, bool]:
    """Target SELECTION (v4 Phase 1): among the eligible rollback candidates (list order = newest
    first, matching every backend), prefer the newest revision the serving-history ledger has
    WITNESSED serving healthily; when the ledger knows none of them (cold start), fall back to the
    newest ready — the v3 recency behavior, byte-identical. Recency is only a PROXY for last-good
    that a bad→bad deploy sequence defeats; a witnessed revision was literally OBSERVED serving
    healthily (at witness time — it can still go stale, which is why selection only PROPOSES: the
    causal pre-check live-probes whatever is chosen before any traffic shifts).
    Returns (revision, from_ledger)."""
    if witnessed:
        for r in candidates:
            if r["name"] in witnessed:
                return r, True
    return (candidates[0] if candidates else None), False


def _heuristic(revs: dict, err: dict, witnessed: dict | None = None) -> dict:
    rs = revs.get("revisions", [])
    serving = next((r for r in rs if r.get("traffic_percent", 0) > 0), None)
    candidates = [r for r in rs if r.get("traffic_percent", 0) == 0 and r.get("ready")]
    healthy, from_ledger = _preferred_target(candidates, witnessed)
    if err.get("error_rate", 0) >= config.ERROR_RATE_THRESHOLD and serving and healthy:
        return {"action": "ROLLBACK", "bad_revision": serving["name"],
                "rollback_revision": healthy["name"], "confidence": 0.9,
                "reasoning": f"5xx rate {err.get('error_rate')} on {serving['name']}; "
                             f"{healthy['name']} is a "
                             + ("WITNESSED-healthy revision (serving-history ledger)."
                                if from_ledger else "healthy prior revision."),
                "evidence": [f"error_rate={err.get('error_rate')}"], "_source": "heuristic",
                "_target_source": "ledger" if from_ledger else "recency"}
    return {"action": "OBSERVE", "confidence": 0.4,
            "reasoning": "no clear bad-revision/healthy-revision pair", "_source": "heuristic"}


def _rollback_pair(revs: dict, witnessed: dict | None = None) -> tuple[str | None, str | None, bool]:
    """Deterministically pick (serving revision, rollback target, target-from-ledger) from the
    revision list — the serving = highest-traffic revision; the target = a ready 0-traffic revision
    that isn't the serving one: WITNESSED-healthy when the ledger knows one, else the newest ready
    (a recency proxy, NOT proven good — see _preferred_target). Mirrors _heuristic's selection;
    used by _validate's promotion."""
    rs = (revs or {}).get("revisions", [])
    serving = max(rs, key=lambda r: r.get("traffic_percent", 0), default=None)
    serving_name = serving["name"] if serving else None
    candidates = [r for r in rs if r.get("traffic_percent", 0) == 0 and r.get("ready")
                  and r["name"] != serving_name]
    target, from_ledger = _preferred_target(candidates, witnessed)
    return serving_name, (target["name"] if target else None), from_ledger


def _serving_revision(revs: dict) -> str | None:
    """The revision actually carrying traffic (highest percent, > 0) — the one a healthy no-op run
    is evidence about. None when nothing serves (nothing to witness)."""
    rs = (revs or {}).get("revisions", [])
    serving = max(rs, key=lambda r: r.get("traffic_percent", 0), default=None)
    return serving["name"] if serving and serving.get("traffic_percent", 0) > 0 else None


def _healthy_witness(stat: dict | None, before: dict) -> bool:
    """Should this no-op run certify the SERVING revision into the witnessed-healthy ledger (v4)?
    Confident evidence only: a PASS verdict, or an OBSERVE with a ZERO observed 5xx rate (at the
    default baseline+sample size a clean 0/N sample is INCONCLUSIVE, not PASS — so zero-rate is the
    live config's witness path). A flaky sub-threshold window (INCONCLUSIVE with errors) must NOT
    certify: the ledger later PROPOSES rollback targets, and a revision that erred while serving
    must never be preferred as "witnessed good" (the live causal pre-check still gates whatever is
    proposed, so a residually-wrong witness cannot bypass the act-time probe)."""
    if stat is not None:
        v = stat.get("verdict")
        return v == "PASS" or (v == "INCONCLUSIVE" and stat.get("rate") == 0.0)
    return (before.get("error_rate") or 0.0) == 0.0


def _primary_signal(stat: dict | None) -> str:
    """Which detector drove the FAIL — '5xx', 'latency', or another key. In single-detector (5xx) mode
    `stat` has no per-signal breakdown, so it's a 5xx incident. Used to describe the incident HONESTLY:
    the forward fix-PR path targets code-bug/5xx incidents (a real KeyError -> 500 the PR repairs); a
    latency regression's remedy is the rollback itself, so we don't fabricate an 'HTTP 500' fix ctx."""
    if not stat:
        return "5xx"                           # no stat gate -> preserve v2 (fix-PR opens)
    sig = stat.get("signals")
    if isinstance(sig, dict):                  # multi-detector mode: trust the per-signal breakdown
        fails = [k for k, v in sig.items() if isinstance(v, dict) and v.get("verdict") == "FAIL"]
        if "5xx" in fails:
            return "5xx"                       # a real 5xx failure -> the code-fix PR path applies
        if fails:
            return fails[0]                    # e.g. 'latency' -> rollback is the remedy, no fix-PR
        return "unknown"                       # FAIL with no single culprit -> don't fabricate a 5xx fix
    return "5xx"                               # single-detector (5xx) mode -> a 5xx incident


def _validate(decision: dict, revs: dict, stat: dict | None = None,
              witnessed: dict | None = None) -> dict:
    """Safety gate + deterministic promotion. The statistical verdict is a DETERMINISTIC signal (the
    detectors, not the LLM), so it can BOTH constrain a ROLLBACK the LLM proposed AND promote a
    rollback the LLM/heuristic missed — without the LLM ever touching prod (FSM acts, LLM advises).
      * ROLLBACK proposed: PASS -> withhold (OBSERVE); INCONCLUSIVE -> ESCALATE; FAIL -> confidence/
        known-good-target gate.
      * non-ROLLBACK proposed + stat FAIL: PROMOTE to a deterministic rollback if a known-good target
        exists, else ESCALATE (degraded but nowhere safe to go). Only on FAIL, never INCONCLUSIVE, so
        a healthy service (INCONCLUSIVE 5xx + OBSERVE) stays a quiet OBSERVE — no alert fatigue.
    `witnessed` (v4) is the serving-history ledger map: the promotion's target selection prefers a
    witnessed-healthy revision over bare recency (cold-start fallback unchanged)."""
    # PROMOTION: a confident (FAIL) statistical verdict drives a rollback even if the LLM hedged.
    if (stat is not None and stat.get("verdict") == "FAIL"
            and decision.get("action") != "ROLLBACK"):
        serving, target, from_ledger = _rollback_pair(revs, witnessed)
        if target:
            return {**decision, "action": "ROLLBACK", "bad_revision": serving,
                    "rollback_revision": target,
                    "confidence": max(decision.get("confidence", 0.0), config.CONFIDENCE_THRESHOLD),
                    "reasoning": f"statistical gate FAIL — {stat.get('reason')}; promoted a rollback "
                                 f"(deterministic multi-signal verdict, not the LLM)"
                                 + (f"; target {target} is WITNESSED-healthy (serving-history ledger)"
                                    if from_ledger else "")
                                 + f". {decision.get('reasoning', '')}",
                    "_promoted": True,
                    "_target_source": "ledger" if from_ledger else "recency"}
        return {**decision, "action": "ESCALATE",
                "reasoning": f"statistical gate FAIL — {stat.get('reason')} — but no eligible "
                             f"rollback target; needs a human. {decision.get('reasoning', '')}"}
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
    # v4 RE-AIM (observed live: Gemini aimed a latency rollback at the KeyError landmine; the causal
    # probe vetoed safely, but a WITNESSED-good target existed — the exact ESCALATE the ledger is
    # built to convert into a heal): under a confident FAIL, if the LLM's proposed target has no
    # witnessed-healthy history but a witnessed candidate exists, the FSM re-aims the rollback at
    # it — deterministic knowledge the LLM lacks, a single substitution (no candidate-walk).
    # GATED ON THE CAUSAL CHECK (adversarial review): overriding an explicit LLM aim is only
    # licensed when the act-time live probe exists to gate the substituted target — with the probe
    # off, the LLM's aim stands (v3 behavior). Documented trade: a STALE witness (healthy once, bad
    # now) makes the probe veto→escalate where the LLM's aim might have healed — the safe direction;
    # and the probe cannot see stateful/migration badness (enable AIRBAG_REVERSIBILITY_GUARD when
    # irreversibility markers are in use — the re-aim prefers OLDER targets). Cold ledger /
    # witnessed proposal / no stat verdict → unchanged.
    if (config.CAUSAL_CHECK_ENABLED and stat is not None and stat.get("verdict") == "FAIL"
            and witnessed and target not in witnessed):
        _, led_target, from_ledger = _rollback_pair(revs, witnessed)
        if from_ledger and led_target and led_target != target:
            return {**decision, "rollback_revision": led_target,
                    "_target_source": "ledger", "_target_overridden": target,
                    "reasoning": f"{decision.get('reasoning', '')} [FSM re-aim: proposed target "
                                 f"{target} has no witnessed-healthy serving history; {led_target} "
                                 f"was WITNESSED serving healthily (serving-history ledger) — the "
                                 f"live causal probe gates it before any traffic shifts.]"}
    return decision


def _verify(service: str, emit, since_epoch: float | None = None, primary_signal: str = "5xx") -> bool:
    """Poll until error-rate is zero AND a synthetic probe succeeds (guards the
    zero-traffic trap: error_rate can read 0 simply because nothing is hitting it).
    `since_epoch` anchors the error window at rollback time (gcp backend).

    SIGNAL-AWARE (v3): we detect on multiple signals, so recovery must be proven on the SAME signal
    that triggered the incident. For a LATENCY incident a slow SUCCESS (200 but past the SLO) does NOT
    count as recovered — otherwise a rollback onto a still-slow revision would read healthy on 5xx while
    the latency regression persists. We gate this on `primary_signal == "latency"` (NOT on "is latency
    enabled") so a 5xx incident is never falsely escalated because its last-good revision is a little
    slow. The retry loop absorbs a one-off cold-start spike on the freshly-served revision (it warms up
    and the next probe is fast); a genuinely-slow target keeps failing every attempt -> escalate."""
    latency_gated = primary_signal == "latency"
    slo_ms = config.LATENCY_SLO_ABS_MS
    for i in range(config.VERIFY_ATTEMPTS):
        err = tools.query_error_rate(service, config.GCP_REGION, window_minutes=2,
                                     since_epoch=since_epoch)
        probe = tools.synthetic_probe(service, path=config.PROBE_PATH)
        elapsed_ms = probe.get("elapsed_ms")
        # a slow-but-200 probe is NOT recovered when latency was the triggering signal
        too_slow = latency_gated and elapsed_ms is not None and elapsed_ms > slo_ms
        probe_ok = bool(probe.get("ok")) and not too_slow
        emit("VERIFYING", f"attempt {i + 1}/{config.VERIFY_ATTEMPTS}",
             error_rate=err.get("error_rate"), total_requests=err.get("total_requests"),
             probe_ok=probe_ok, latency_ms=elapsed_ms)
        if probe_ok and err.get("error_rate", 1) == 0:
            return True
        time.sleep(config.VERIFY_INTERVAL_S)
    return False
