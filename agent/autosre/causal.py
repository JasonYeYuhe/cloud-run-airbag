"""Causal pre-check (v3 Phase 2a) — bind the symptom to the deploy before spending the rollback.

The one reversible action (roll traffic to the last-good revision) only helps if THIS revision is the
cause. An out-of-window bad deploy and a coincident dependency/quota outage are temporally
indistinguishable ("current revision serving, symptom now") — the reliable disambiguator is: **does
the rollback TARGET (the last-good revision) exhibit the same failure?**
  * bad deploy → the prior revision is healthy → rollback HELPS      → CAUSAL (proceed)
  * dependency → the prior revision ALSO fails  → rollback is FUTILE → COINCIDENT (escalate, don't roll back)

SAFETY: only a CONFIDENT-unhealthy target blocks the rollback (Wilson lower bound over CAUSAL_PROBE_N,
reusing analyzer.analyze — the same rigor as the 5xx gate). A transient blip, a cold-start on a
scaled-to-zero target, a flaky probe, or a probe error → INCONCLUSIVE → PROCEED with the rollback.
The pre-check only PREVENTS a futile rollback; it never blocks a legitimate one, and the existing
post-rollback `_verify` remains the backstop. DETERMINISTIC + LLM-FREE (guarded by the AST invariant).

This does NOT add a new correctness class for stateful/data-migration bugs (a synthetic GET on the
probe path doesn't exercise a mutation); its win is skipping a futile traffic shift + the verify
delay, and giving an evidence-grounded pre-action reason for the RCA.
"""
from __future__ import annotations

import logging

from . import analyzer, config, tools

log = logging.getLogger("airbag.causal")


def precheck(service: str, region: str, target: str | None, primary_signal: str = "5xx") -> dict:
    """Probe the rollback target's health. Returns {verdict, reason, target, probe} where verdict is:
      COINCIDENT   — target CONFIDENTLY also degraded → the cause is external → do NOT roll back.
      CAUSAL       — target confidently healthy → this revision is the likely cause → roll back.
      INCONCLUSIVE — can't confirm the target is broken → PROCEED with the rollback (fail-safe).

    `primary_signal` (v4 Phase 2) keys a SECOND gate on the axis that actually triggered the
    incident: for a LATENCY incident, a target that is confidently SLOW (Wilson gate on the probe's
    slow-proportion, same knobs as the latency detector) is ALSO a futile rollback → COINCIDENT.
    The 5xx gate below runs for every incident unchanged (a 5xx-broken target fails `_verify` on
    any signal, so vetoing it pre-shift is always right); the latency gate is VETO-ONLY and adds no
    selection/walk. For `primary_signal="5xx"` the DECISION behavior (verdicts, gating, reason
    strings) is identical to v3; the persisted probe payload additionally carries the slow count.
    HONEST LIMITS: the gcp probe rinses the cold start (an untimed warmup request) and drops
    unreachable samples, so the latency veto sees slowness between the SLO and the probe's 10s
    client timeout — an extreme (>10s) external slowness reads INCONCLUSIVE → proceed, and the
    post-rollback `_verify` remains the backstop (fail-open by design, never fail-closed)."""
    if not target:
        return {"verdict": "INCONCLUSIVE", "reason": "no rollback target to probe", "target": target}
    try:
        probe = tools.probe_revision_health(service, region, target, config.CAUSAL_PROBE_N)
    except Exception as e:  # noqa: BLE001 — a probe error must never block a legitimate rollback
        log.warning("causal target-probe failed (%s); proceeding with the rollback", e)
        return {"verdict": "INCONCLUSIVE", "reason": f"target probe errored ({e}); proceeding", "target": target}

    errs, total = int(probe.get("errs", 0)), int(probe.get("total", 0))
    slow = int(probe.get("slow", 0))
    seen = {"errs": errs, "total": total, "slow": slow}
    if total <= 0:
        return {"verdict": "INCONCLUSIVE", "reason": "no target-probe samples; proceeding",
                "target": target, "probe": probe}
    v = analyzer.analyze(errs, total, config.CAUSAL_TOLERANCE,
                         z=config.STAT_Z, min_fail_errors=config.CAUSAL_MIN_ERRORS)
    if v["verdict"] == "FAIL":   # the last-good target is CONFIDENTLY also failing → external cause
        return {"verdict": "COINCIDENT", "target": target, "probe": seen,
                "reason": (f"rollback target {target} is ALSO degraded ({errs}/{total} probe failures, "
                           f"CI lower > {config.CAUSAL_TOLERANCE:.0%}) — the cause is external "
                           f"(dependency/quota), not this revision; a rollback is futile")}
    if primary_signal == "latency":
        # v4: the probe axis must match the incident's axis — a 200-but-slow target passes the 5xx
        # gate yet cannot remedy a LATENCY incident (post-rollback _verify would fail on the SLO).
        # Same statistical rigor + knobs as the latency detector; below-the-bar warmup blips stay
        # INCONCLUSIVE → proceed (never block a legit rollback on a cold-start wobble).
        sv = analyzer.analyze(slow, total, config.LATENCY_SLO_TOLERANCE,
                              z=config.STAT_Z, min_fail_errors=config.LATENCY_MIN_SLOW)
        if sv["verdict"] == "FAIL":
            return {"verdict": "COINCIDENT", "target": target, "probe": seen,
                    "reason": (f"latency incident and rollback target {target} is ALSO confidently "
                               f"slow ({slow}/{total} probe requests over the "
                               f"{config.LATENCY_SLO_ABS_MS:.0f}ms SLO, CI lower > "
                               f"{config.LATENCY_SLO_TOLERANCE:.0%}) — the slowness is external, "
                               f"not this revision; a rollback is futile")}
    # PASS (confidently healthy — only reachable at a large probe N) → CAUSAL; otherwise INCONCLUSIVE.
    # Both PROCEED with the rollback, so at the default N the practical outcomes are COINCIDENT (block)
    # vs INCONCLUSIVE (proceed) — the conservative, never-block-a-legit-rollback posture.
    verdict = "CAUSAL" if v["verdict"] == "PASS" else "INCONCLUSIVE"
    return {"verdict": verdict, "target": target, "probe": seen,
            "reason": (f"rollback target {target} is not confidently degraded "
                       f"({errs}/{total} probe failures"
                       + (f", {slow}/{total} slow" if primary_signal == "latency" else "")
                       + ") — proceeding with the rollback")}
