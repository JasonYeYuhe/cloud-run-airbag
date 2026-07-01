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


def precheck(service: str, region: str, target: str | None) -> dict:
    """Probe the rollback target's health. Returns {verdict, reason, target, probe} where verdict is:
      COINCIDENT   — target CONFIDENTLY also degraded → the cause is external → do NOT roll back.
      CAUSAL       — target confidently healthy → this revision is the likely cause → roll back.
      INCONCLUSIVE — can't confirm the target is broken → PROCEED with the rollback (fail-safe)."""
    if not target:
        return {"verdict": "INCONCLUSIVE", "reason": "no rollback target to probe", "target": target}
    try:
        probe = tools.probe_revision_health(service, region, target, config.CAUSAL_PROBE_N)
    except Exception as e:  # noqa: BLE001 — a probe error must never block a legitimate rollback
        log.warning("causal target-probe failed (%s); proceeding with the rollback", e)
        return {"verdict": "INCONCLUSIVE", "reason": f"target probe errored ({e}); proceeding", "target": target}

    errs, total = int(probe.get("errs", 0)), int(probe.get("total", 0))
    if total <= 0:
        return {"verdict": "INCONCLUSIVE", "reason": "no target-probe samples; proceeding",
                "target": target, "probe": probe}
    v = analyzer.analyze(errs, total, config.CAUSAL_TOLERANCE,
                         z=config.STAT_Z, min_fail_errors=config.CAUSAL_MIN_ERRORS)
    if v["verdict"] == "FAIL":   # the last-good target is CONFIDENTLY also failing → external cause
        return {"verdict": "COINCIDENT", "target": target, "probe": {"errs": errs, "total": total},
                "reason": (f"rollback target {target} is ALSO degraded ({errs}/{total} probe failures, "
                           f"CI lower > {config.CAUSAL_TOLERANCE:.0%}) — the cause is external "
                           f"(dependency/quota), not this revision; a rollback is futile")}
    # PASS (confidently healthy — only reachable at a large probe N) → CAUSAL; otherwise INCONCLUSIVE.
    # Both PROCEED with the rollback, so at the default N the practical outcomes are COINCIDENT (block)
    # vs INCONCLUSIVE (proceed) — the conservative, never-block-a-legit-rollback posture.
    verdict = "CAUSAL" if v["verdict"] == "PASS" else "INCONCLUSIVE"
    return {"verdict": verdict, "target": target, "probe": {"errs": errs, "total": total},
            "reason": (f"rollback target {target} is not confidently degraded "
                       f"({errs}/{total} probe failures) — proceeding with the rollback")}
