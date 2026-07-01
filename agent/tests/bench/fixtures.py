"""Airbag-Bench fixture corpus — labeled incident scenarios.

Each case describes a controlled "world" (the signals Airbag observes) plus the GROUND-TRUTH correct
action and a written rationale for the label. The harness replays the real ``run_self_heal`` over
each world (LLM off — the deterministic ``_heuristic`` floor) and scores the decided action vs the
ground truth. See ``docs/AIRBAG_BENCH.md`` for methodology + per-case rationale.

IMPORTANT — two independent signals feed two different gates (this is why a single "5xx number" is
insufficient, and why both are pinned per case):
  * ``error_rate``        -> ``tools.query_error_rate``     -> consumed by ``_heuristic`` (threshold 0.05)
  * ``sample {errs,total}`` -> ``tools.sample_business_path`` -> consumed by ``analyzer.analyze`` (Wilson gate)
``_heuristic`` decides the *action* FIRST; ``analyzer`` only produces the FAIL/PASS/INCONCLUSIVE
constraint the gate (``_validate``) applies to a ROLLBACK. So a case's decided action depends on BOTH.

Forward-compat: ``latency_p99_ms`` / ``saturation`` are already carried even though the v2 5xx-only
detector ignores them. Phase 1's multi-signal detector will consume them; the v2 MISS on those cases
IS the baseline recall gap (a SIGNAL-COVERAGE gap, not a gate bug — with 5xx~0 both the heuristic and
the stat gate are blind).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Action = Literal["ROLLBACK", "OBSERVE", "ESCALATE"]


@dataclass(frozen=True)
class BenchCase:
    name: str
    category: str                 # crash | latency | saturation | slo_burn | dependency | blip | healthy | stuck
    description: str
    world: dict                   # see FixtureBackend for the consumed fields
    expected_action: Action       # GROUND TRUTH: the action a correct agent should take
    is_bad_deploy: bool           # was this a deploy-caused regression that warranted a rollback?
    rationale: str                # WHY this label (esp. for the judgment-call cases) — committed honesty


# Two ready revisions: a bad one serving 100% and a healthy prior at 0% (a valid rollback target).
def _bad_and_good(svc: str = "airbag-target") -> list[dict]:
    return [
        {"name": f"{svc}-00002-bad", "ready": True, "traffic_percent": 100,
         "create_time": "2026-06-28T00:00:00Z"},
        {"name": f"{svc}-00001-good", "ready": True, "traffic_percent": 0,
         "create_time": "2026-06-27T22:00:00Z"},
    ]


# A healthy service: current serving 100%, an older ready revision at 0%.
def _healthy_pair(svc: str = "airbag-target") -> list[dict]:
    return [
        {"name": f"{svc}-00005-cur", "ready": True, "traffic_percent": 100,
         "create_time": "2026-06-29T00:00:00Z"},
        {"name": f"{svc}-00004-prev", "ready": True, "traffic_percent": 0,
         "create_time": "2026-06-28T20:00:00Z"},
    ]


_KEYERROR_TRACE = ('Traceback (most recent call last):\n  File "main.py", line 55, in orders\n'
                   "    ... total_revenue(ORDERS, buggy=True)\n  File \"main.py\", line 46, in "
                   "total_revenue\n    return sum(o[key] for o in orders)\nKeyError: 'amount'")


def _lat(slow: int, total: int = 20, n: int = 4) -> list[dict]:
    """N identical latency windows, each with `slow` of `total` requests over the SLO (a SUSTAINED
    regression the debounce accepts)."""
    return [{"slow": slow, "total": total} for _ in range(n)]


CASES: list[BenchCase] = [
    # --- v2 floor gets these RIGHT (rollback) -----------------------------------------------------
    BenchCase(
        name="crash_total_outage", category="crash",
        description="Bad revision serving 100%; the business path 5xxs on every request.",
        world={"revisions": _bad_and_good(), "error_rate": 1.0, "sample": {"errs": 20, "total": 20},
               "rollback_clears": True, "logs": [_KEYERROR_TRACE]},
        expected_action="ROLLBACK", is_bad_deploy=True,
        rationale="Unambiguous crash with a healthy prior revision — auto-rollback is correct and "
                  "reversible. The v2 floor catches it (error_rate 1.0 >= 0.05; Wilson FAIL)."),
    BenchCase(
        name="crash_high_5xx", category="crash",
        description="New revision 5xxs on the majority of requests (0.65), healthy prior exists.",
        world={"revisions": _bad_and_good(), "error_rate": 0.65, "sample": {"errs": 14, "total": 20},
               "rollback_clears": True, "logs": [_KEYERROR_TRACE]},
        expected_action="ROLLBACK", is_bad_deploy=True,
        rationale="High, statistically-confident 5xx rate above BOTH the heuristic (0.05) and the "
                  "live-LLM (0.5) thresholds — a clean rollback anchor that doesn't depend on which "
                  "decider is used."),

    # --- v2 floor gets these RIGHT (observe / tolerate) -------------------------------------------
    BenchCase(
        name="healthy_steady", category="healthy",
        description="Service is healthy at real traffic — zero 5xx in the sample.",
        world={"revisions": _healthy_pair(), "error_rate": 0.0, "sample": {"errs": 0, "total": 20},
               "rollback_clears": True},
        expected_action="OBSERVE", is_bad_deploy=False,
        rationale="No degradation; the only correct action is to observe and fold a healthy baseline "
                  "sample. v2 correctly OBSERVEs (error_rate 0.0 < 0.05)."),
    BenchCase(
        name="healthy_noisy", category="healthy",
        description="Healthy service with sub-threshold background noise (3% windowed, 2 errs/40).",
        world={"revisions": _healthy_pair(), "error_rate": 0.03, "sample": {"errs": 2, "total": 40},
               "rollback_clears": True},
        expected_action="OBSERVE", is_bad_deploy=False,
        rationale="Background noise below the 5% instant threshold AND below min_fail_errors=3 — a "
                  "mature agent must NOT page. v2 correctly OBSERVEs. (Contrast low_traffic_blip, "
                  "where a windowed rate crosses 0.05 and v2 over-escalates.)"),
    BenchCase(
        name="recovered_before_heal", category="healthy",
        description="An alert fired, but by triage the service has self-recovered (residual noise only).",
        world={"revisions": _healthy_pair(), "error_rate": 0.02, "sample": {"errs": 0, "total": 20},
               "rollback_clears": True},
        expected_action="OBSERVE", is_bad_deploy=False,
        rationale="The out-of-window monitor must re-sample at decision time and NOT roll back a "
                  "service that already recovered. v2's active sampling correctly OBSERVEs."),

    # --- v2 floor MISSES these: SIGNAL-COVERAGE gap (5xx-only) -> Phase 1 multi-signal -------------
    BenchCase(
        name="latency_regression", category="latency",
        description="p99 latency regressed ~14x (320ms -> 4500ms) with NO 5xx — requests succeed, "
                    "slowly, in every window.",
        world={"revisions": _bad_and_good(), "error_rate": 0.0, "sample": {"errs": 0, "total": 20},
               "rollback_clears": True, "baseline_latency_ms": 320,
               "latency_windows": _lat(18, 20)},   # 18/20 requests over SLO, sustained across 4 windows
        expected_action="ROLLBACK", is_bad_deploy=True,
        rationale="Canonical out-of-window bad deploy. 5xx-only MISSES it (sample 0/20 -> heuristic + "
                  "Wilson both blind -> OBSERVE); the Phase 1.2 latency detector confidently FAILs "
                  "(18/20 over SLO across 4 windows) and the promotion drives a rollback."),
    BenchCase(
        name="latency_regression_moderate", category="latency",
        description="A MODERATE ~2.5x p99 regression (320ms -> ~950ms), 40% of requests over SLO, "
                    "sustained — tests the detector's sensitivity floor, not just the 14x extreme.",
        world={"revisions": _bad_and_good(), "error_rate": 0.0, "sample": {"errs": 0, "total": 20},
               "rollback_clears": True, "baseline_latency_ms": 320,
               "latency_windows": _lat(8, 20)},    # 8/20 over SLO — still Wilson-confident, sustained
        expected_action="ROLLBACK", is_bad_deploy=True,
        rationale="A second latency positive at a different magnitude, so recall can't be an artifact "
                  "of one fixture hand-matched to the threshold. Multi-signal catches it; 5xx misses."),
    BenchCase(
        name="latency_within_slo", category="healthy",
        description="Mild latency jitter WITHIN the SLO (1/20 requests slow, below tolerance), "
                    "sustained — the detector must NOT over-fire on normal variance.",
        world={"revisions": _healthy_pair(), "error_rate": 0.0, "sample": {"errs": 0, "total": 20},
               "rollback_clears": True, "baseline_latency_ms": 320,
               "latency_windows": _lat(1, 20)},    # 1/20 over SLO — below LATENCY_MIN_SLOW -> PASS
        expected_action="OBSERVE", is_bad_deploy=False,
        rationale="Sub-threshold sustained negative: proves the latency detector doesn't roll back on "
                  "normal latency variance (Wilson + min-slow gate)."),
    BenchCase(
        name="latency_spike_transient", category="latency",
        description="A single hot window (a GC pause / cold start): 18/20 slow in ONE window, clean in "
                    "the other three — a momentary spike, NOT a sustained regression.",
        world={"revisions": _healthy_pair(), "error_rate": 0.0, "sample": {"errs": 0, "total": 20},
               "rollback_clears": True, "baseline_latency_ms": 320,
               "latency_windows": [{"slow": 18, "total": 20}, {"slow": 0, "total": 20},
                                   {"slow": 0, "total": 20}, {"slow": 0, "total": 20}]},
        expected_action="OBSERVE", is_bad_deploy=False,
        rationale="ANTI-FLAP negative: a NAIVE non-debounced detector WOULD trip on window 1, but the "
                  "N-window persistence gate (1/4 < 3-window debounce) collapses to PASS -> OBSERVE. "
                  "This is what makes the debounce provably load-bearing."),
    BenchCase(
        name="saturation_cpu", category="saturation",
        description="CPU 98% / mem 95% / instance pressure; only sporadic 5xx (1%).",
        world={"revisions": _bad_and_good(), "error_rate": 0.01, "sample": {"errs": 0, "total": 20},
               "rollback_clears": True, "saturation": {"cpu": 0.98, "mem": 0.95}},
        expected_action="ROLLBACK", is_bad_deploy=True,
        rationale="Resource saturation from a bad deploy. Pre-registered RECALL GAP: error_rate 0.01 "
                  "< 0.05 -> v2 OBSERVEs. Phase 1's saturation detector closes this."),
    BenchCase(
        name="slo_slow_burn", category="slo_burn",
        description="Sustained 3% error budget burn — individually sub-threshold, but burning SLO over hours.",
        world={"revisions": _bad_and_good(), "error_rate": 0.03, "sample": {"errs": 1, "total": 40},
               "rollback_clears": True, "logs": [_KEYERROR_TRACE]},
        expected_action="ROLLBACK", is_bad_deploy=True,
        rationale="A slow burn below the instant 5% threshold but exhausting the error budget. "
                  "Pre-registered RECALL GAP: v2's single-window 0.05 check OBSERVEs. Phase 1's "
                  "multi-window burn-rate detector closes this."),

    # --- CAUSAL gap -> Phase 2a causal pre-check (probe the rollback target before committing) -------
    # target_probe is the per-revision probe MODEL the causal check Wilson-gates in-bench; the coupling
    # (enforced by a test) is: rollback_clears==False  <=>  the target is confidently unhealthy, because
    # an external cause (dependency/quota) breaks the target revision too.
    BenchCase(
        name="coincident_dependency_outage", category="dependency",
        description="High 5xx (18/20) but the cause is an upstream DB outage, NOT the deploy; the "
                    "rollback target ALSO fails, so rolling back does not clear the errors.",
        world={"revisions": _healthy_pair(), "error_rate": 0.90, "sample": {"errs": 18, "total": 20},
               "rollback_clears": False, "target_probe": {"errs": 8, "total": 8},
               "logs": ["psycopg2.OperationalError: could not connect to server: Connection refused"]},
        expected_action="ESCALATE", is_bad_deploy=False,
        rationale="PRECISION GAP: 5xx-only sees 18/20 + a healthy prior and rolls back — wasting the one "
                  "reversible action (the DB is down for ALL revisions) then escalating. Phase 2a's "
                  "causal pre-check probes the rollback target, finds it ALSO failing (8/8) → COINCIDENT "
                  "→ ESCALATE WITHOUT the futile traffic shift. (v2 was already SAFE via post-rollback "
                  "_verify; the win is skipping the wasted shift + verify delay + a pre-action reason.)"),
    BenchCase(
        name="coincident_quota_exhaustion", category="dependency",
        description="High 5xx (17/20) from an exhausted upstream quota — a second external cause; the "
                    "rollback target is ALSO throttled.",
        world={"revisions": _healthy_pair(), "error_rate": 0.85, "sample": {"errs": 17, "total": 20},
               "rollback_clears": False, "target_probe": {"errs": 8, "total": 8},
               "logs": ["googleapi: Error 429: Quota exceeded for quota metric 'Requests'"]},
        expected_action="ESCALATE", is_bad_deploy=False,
        rationale="A SECOND external-cause fixture so 100% precision isn't a one-fixture artifact. Same "
                  "causal logic: target also failing → COINCIDENT → ESCALATE, no wasted rollback."),
    BenchCase(
        name="masquerade_real_bad_deploy", category="crash",
        description="Looks like a dependency outage (high 5xx, 15/20) but it IS a bad deploy — the "
                    "rollback target is HEALTHY, so the causal check must NOT block the rollback.",
        world={"revisions": _bad_and_good(), "error_rate": 0.75, "sample": {"errs": 15, "total": 20},
               "rollback_clears": True, "target_probe": {"errs": 0, "total": 8},
               "logs": [_KEYERROR_TRACE]},
        expected_action="ROLLBACK", is_bad_deploy=True,
        rationale="ANTI-REGRESSION: the causal check must not turn a genuine bad deploy into an escalate. "
                  "Target probes HEALTHY (0/8) → CAUSAL/INCONCLUSIVE → PROCEED → ROLLBACK, with the "
                  "causal check ON."),
    BenchCase(
        name="intermittent_target", category="crash",
        description="A real bad deploy (15/20 5xx) whose rollback target has INTERMITTENT blips (2/8) "
                    "below the confidence bar — the causal check must PROCEED, not block on a flaky probe.",
        world={"revisions": _bad_and_good(), "error_rate": 0.75, "sample": {"errs": 15, "total": 20},
               "rollback_clears": True, "target_probe": {"errs": 2, "total": 8},
               "logs": [_KEYERROR_TRACE]},
        expected_action="ROLLBACK", is_bad_deploy=True,
        rationale="SAFETY: a transient/cold-start blip on the target (2/8, below CAUSAL_MIN_ERRORS/CI) "
                  "must resolve to INCONCLUSIVE → PROCEED, never a confident-false COINCIDENT that would "
                  "block a legitimate rollback (protecting a bad revision is the worst failure)."),

    # --- v2 floor OVER-ESCALATES: thin-evidence paging -> Phase 3 verifier / better evidence --------
    BenchCase(
        name="low_traffic_blip", category="blip",
        description="A single 5xx in a 4-request window (windowed rate 25%, but 1 error total).",
        world={"revisions": _healthy_pair(), "error_rate": 0.25, "sample": {"errs": 1, "total": 4},
               "rollback_clears": True},
        expected_action="OBSERVE", is_bad_deploy=False,
        rationale="JUDGMENT CALL / paging policy (pre-registered FALSE-ESCALATION gap): a lone 5xx in a "
                  "tiny window is below the noise floor; the policy here is that a mature agent should "
                  "treat < min_fail_errors as steady-state noise and OBSERVE, not page. v2 over-"
                  "escalates: heuristic ROLLBACK (0.25>=0.05) but the Wilson gate is INCONCLUSIVE "
                  "(1<3 errors) -> ESCALATE. (A team that pages on any 5xx would label this ESCALATE.)"),

    # --- v2 floor MISHANDLES: a real outage with no safe target -> should ESCALATE, not OBSERVE ----
    BenchCase(
        name="no_healthy_target", category="stuck",
        description="Real outage (15/20 5xx) but the only other revision is NOT ready — nowhere safe "
                    "to roll back to.",
        world={"revisions": [
                   {"name": "airbag-target-00002-bad", "ready": True, "traffic_percent": 100,
                    "create_time": "2026-06-28T00:00:00Z"},
                   {"name": "airbag-target-00001-old", "ready": False, "traffic_percent": 0,
                    "create_time": "2026-06-27T22:00:00Z"}],
               "error_rate": 0.75, "sample": {"errs": 15, "total": 20},
               "rollback_clears": True, "logs": [_KEYERROR_TRACE]},
        expected_action="ESCALATE", is_bad_deploy=True,
        rationale="JUDGMENT CALL (pre-registered MISHANDLE): a confirmed outage with no ready rollback "
                  "target should page a human. v2's heuristic silently OBSERVEs (no healthy/serving "
                  "pair) — a real, quiet gap. (The live LLM might instead propose a rollback to a "
                  "not-ready revision, which _validate would ESCALATE — so live behavior may differ; "
                  "the floor under-reacts.)"),
]


def case_by_name(name: str) -> BenchCase:
    for c in CASES:
        if c.name == name:
            return c
    raise KeyError(name)
