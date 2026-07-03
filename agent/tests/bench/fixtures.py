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
    category: str                 # crash | latency | saturation | slo_burn | dependency | blip | healthy | stuck | bad_bad
    description: str
    world: dict                   # see FixtureBackend for the consumed fields
    expected_action: Action       # GROUND TRUTH: the action a correct agent should take
    is_bad_deploy: bool           # was this a deploy-caused regression that warranted a rollback?
    rationale: str                # WHY this label (esp. for the judgment-call cases) — committed honesty
    expected_target: str | None = None   # GROUND TRUTH rollback target (v4): the revision a correct
                                         # agent should aim the rollback at. Only set when
                                         # expected_action == ROLLBACK; scored as TARGET-correctness.


_GOOD = "airbag-target-00001-good"   # the healthy prior in _bad_and_good — every 2-revision
                                     # ROLLBACK case's ground-truth target (the only candidate)


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

# The bad→bad (v4 target-correctness) world: newest serving is bad, the newest READY 0-traffic
# revision is a SECOND bad deploy (the landmine recency would aim at), and an older revision is
# the genuinely-good one the ledger has witnessed.
_BB_LANDMINE = "airbag-target-00011-landmine"
_BB_GOOD = "airbag-target-00009-good"


def _bad_bad_revs(svc: str = "airbag-target") -> list[dict]:
    return [
        {"name": f"{svc}-00012-bad", "ready": True, "traffic_percent": 100,
         "create_time": "2026-07-01T12:00:00Z"},
        {"name": _BB_LANDMINE, "ready": True, "traffic_percent": 0,
         "create_time": "2026-07-01T10:00:00Z"},
        {"name": _BB_GOOD, "ready": True, "traffic_percent": 0,
         "create_time": "2026-06-30T08:00:00Z"},
    ]


def _lat(slow: int, total: int = 20, n: int = 4) -> list[dict]:
    """N identical latency windows, each with `slow` of `total` requests over the SLO (a SUSTAINED
    regression the debounce accepts)."""
    return [{"slow": slow, "total": total} for _ in range(n)]


def _burn(errs: int, total: int = 50, n: int = 6) -> list[dict]:
    """N identical error windows, each `errs`/`total` 5xx (v5 5.1 burn-rate pooling). Each window is
    individually sub-threshold for a single-window Wilson gate; POOLED over the windows the LB clears
    the baseline, and errors present in all N windows makes it a SUSTAINED burn (not a spike)."""
    return [{"errs": errs, "total": total} for _ in range(n)]


CASES: list[BenchCase] = [
    # --- v2 floor gets these RIGHT (rollback) -----------------------------------------------------
    BenchCase(
        name="crash_total_outage", category="crash",
        description="Bad revision serving 100%; the business path 5xxs on every request.",
        world={"revisions": _bad_and_good(), "error_rate": 1.0, "sample": {"errs": 20, "total": 20},
               "rollback_clears": True, "logs": [_KEYERROR_TRACE]},
        expected_action="ROLLBACK", is_bad_deploy=True, expected_target=_GOOD,
        rationale="Unambiguous crash with a healthy prior revision — auto-rollback is correct and "
                  "reversible. The v2 floor catches it (error_rate 1.0 >= 0.05; Wilson FAIL)."),
    BenchCase(
        name="crash_high_5xx", category="crash",
        description="New revision 5xxs on the majority of requests (0.65), healthy prior exists.",
        world={"revisions": _bad_and_good(), "error_rate": 0.65, "sample": {"errs": 14, "total": 20},
               "rollback_clears": True, "logs": [_KEYERROR_TRACE]},
        expected_action="ROLLBACK", is_bad_deploy=True, expected_target=_GOOD,
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
               # v5 5.1 anti-false-fire: SUSTAINED benign ~3% noise (9/300 pooled) — ABOVE the 2%
               # baseline yet the pooled Wilson LB (~1.6%) does NOT confidently clear it, so the burn
               # detector correctly PASSes. A benign-noisy service is not a burn.
               "error_windows": [{"errs": 2, "total": 50}] * 3 + [{"errs": 1, "total": 50}] * 3,
               "rollback_clears": True},
        expected_action="OBSERVE", is_bad_deploy=False,
        rationale="Background noise below the 5% instant threshold AND below min_fail_errors=3 — a "
                  "mature agent must NOT page. v2 correctly OBSERVEs; the v5 burn detector also PASSes "
                  "(3% pooled LB doesn't clear the 2% baseline). (Contrast slo_slow_burn's 4% burn.)"),
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
        expected_action="ROLLBACK", is_bad_deploy=True, expected_target=_GOOD,
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
        expected_action="ROLLBACK", is_bad_deploy=True, expected_target=_GOOD,
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
        expected_action="ROLLBACK", is_bad_deploy=True, expected_target=_GOOD,
        rationale="Resource saturation from a bad deploy. Pre-registered RECALL GAP: error_rate 0.01 "
                  "< 0.05 -> v2 OBSERVEs. Phase 1's saturation detector closes this."),
    BenchCase(
        name="slo_slow_burn", category="slo_burn",
        description="Sustained 3% error budget burn — individually sub-threshold, but burning SLO over hours.",
        world={"revisions": _bad_and_good(), "error_rate": 0.03, "sample": {"errs": 1, "total": 40},
               "error_windows": _burn(2, 50, 6),   # v5 5.1: pooled 12/300 -> Wilson LB clears baseline
               "rollback_clears": True, "logs": [_KEYERROR_TRACE]},
        expected_action="ROLLBACK", is_bad_deploy=True, expected_target=_GOOD,
        rationale="A slow burn below the instant 5% threshold but exhausting the error budget. "
                  "Pre-registered RECALL GAP: v2's single-window 0.05 check + single-window Wilson (1/40 "
                  "≈ 0.45% LB) both OBSERVE. v5 5.1's POOLED-Wilson burn-rate detector (12/300 across 6 "
                  "windows, LB > baseline, sustained) closes it — a ROLLBACK when 'burn' is enabled."),

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
        expected_action="ROLLBACK", is_bad_deploy=True, expected_target=_GOOD,
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
        expected_action="ROLLBACK", is_bad_deploy=True, expected_target=_GOOD,
        rationale="SAFETY: a transient/cold-start blip on the target (2/8, below CAUSAL_MIN_ERRORS/CI) "
                  "must resolve to INCONCLUSIVE → PROCEED, never a confident-false COINCIDENT that would "
                  "block a legitimate rollback (protecting a bad revision is the worst failure)."),

    # --- v4 LATENCY axis on the causal probe: the target-probe must match the incident's SIGNAL ---
    BenchCase(
        name="latency_coincident_slow_target", category="dependency",
        description="A sustained latency regression whose cause is an EXTERNAL slow dependency — "
                    "the rollback target responds 200 but is ALSO confidently slow (8/8 over SLO), "
                    "so rolling back cannot remedy the latency incident.",
        world={"revisions": _healthy_pair(), "error_rate": 0.0, "sample": {"errs": 0, "total": 20},
               "rollback_clears": False, "baseline_latency_ms": 320,
               "latency_windows": _lat(18, 20),
               "target_probe": {"errs": 0, "total": 8, "slow": 8},
               "logs": ["upstream db: query latency p99 4200ms (connection pool saturated)"]},
        expected_action="ESCALATE", is_bad_deploy=False,
        rationale="The v4 latency-axis PRECISION gap: the v3 causal probe counts only 5xx, so a "
                  "200-but-slow target passes (0/8 errs) and the futile rollback ships, fails "
                  "verify, and escalates late. The latency-keyed probe sees 8/8 over-SLO → "
                  "COINCIDENT → ESCALATE with zero traffic shifted. (5xx-only mode is blind to the "
                  "whole incident — a pre-registered detection miss, same as latency_regression.)"),
    BenchCase(
        name="latency_target_warmup_blip", category="latency",
        description="A REAL latency bad-deploy whose rollback target shows a small cold-start "
                    "wobble in the probe (2/8 slow, below the confidence bar) — the latency-keyed "
                    "veto must NOT block the legitimate rollback.",
        world={"revisions": _bad_and_good(), "error_rate": 0.0, "sample": {"errs": 0, "total": 20},
               "rollback_clears": True, "baseline_latency_ms": 320,
               "latency_windows": _lat(18, 20),
               "target_probe": {"errs": 0, "total": 8, "slow": 2}},
        expected_action="ROLLBACK", is_bad_deploy=True, expected_target=_GOOD,
        rationale="SAFETY anti-regression for the latency axis (mirrors intermittent_target on the "
                  "5xx axis): a scaled-to-zero target's warmup blip (2/8 < LATENCY_MIN_SLOW) must "
                  "resolve INCONCLUSIVE → PROCEED — never a confident-false COINCIDENT that would "
                  "protect the slow revision."),

    # --- v4 TARGET-correctness: bad→bad deploys — recency aims at a landmine; the serving-history
    # ledger aims at the witnessed-good revision. World: THREE revisions — newest serving (bad),
    # a newer-ready 0-traffic LANDMINE (also bad: the panic-ship), an older witnessed-good.
    # `witnessed` seeds the ledger pre-run; `clears_on`/`target_probes` make outcomes and probes
    # PER-REVISION (rolling onto the landmine does not clear; probing it shows it degraded).
    BenchCase(
        name="bad_bad_ledger_heals", category="bad_bad",
        description="Two consecutive bad deploys (ship broken, panic-ship broken again). The newest "
                    "ready 0-traffic revision is the second landmine; an OLDER revision was "
                    "witnessed serving healthily. The ledger must aim the rollback at it.",
        world={"revisions": _bad_bad_revs(), "error_rate": 0.9, "sample": {"errs": 18, "total": 20},
               "witnessed": [_BB_GOOD], "clears_on": [_BB_GOOD],
               "target_probes": {_BB_LANDMINE: {"errs": 8, "total": 8}},
               "logs": [_KEYERROR_TRACE]},
        expected_action="ROLLBACK", is_bad_deploy=True, expected_target=_BB_GOOD,
        rationale="THE v4 MARQUEE POSITIVE: recency-as-last-good is defeated by a bad→bad sequence "
                  "(it aims at the landmine — see bad_bad_cold_ledger, the matched pre-v4 control); "
                  "the witnessed-healthy ledger aims at the proven-good older revision, the live "
                  "causal probe agrees, and the heal is autonomous. Scored on TARGET, not just action."),
    BenchCase(
        name="bad_bad_cold_ledger", category="bad_bad",
        description="The SAME bad→bad world with a COLD ledger (no witnessed history) — the pre-v4 "
                    "behavior: recency aims the rollback at the landmine.",
        world={"revisions": _bad_bad_revs(), "error_rate": 0.9, "sample": {"errs": 18, "total": 20},
               "clears_on": [_BB_GOOD],
               "target_probes": {_BB_LANDMINE: {"errs": 8, "total": 8}},
               "logs": [_KEYERROR_TRACE]},
        expected_action="ROLLBACK", is_bad_deploy=True, expected_target=_BB_GOOD,
        rationale="THE MATCHED CONTROL (committed proof of the OLD behavior): cold start falls back "
                  "to recency, which lands on the landmine — causal OFF: a WASTED rollback (shift, "
                  "fail verify, escalate); causal ON: the live probe vetoes and a human is paged "
                  "even though a proven-good revision exists. Either way the TARGET is wrong — the "
                  "dimension the action-only scorecard could not see. The ledger case above heals."),
    BenchCase(
        name="bad_deploy_newest_is_witnessed", category="bad_bad",
        description="NEGATIVE CONTROL: one bad deploy over a healthy-newest baseline that IS "
                    "witnessed — the ledger and recency agree on the same target.",
        world={"revisions": _bad_and_good(), "error_rate": 0.9, "sample": {"errs": 18, "total": 20},
               "witnessed": [_GOOD], "rollback_clears": True, "logs": [_KEYERROR_TRACE]},
        expected_action="ROLLBACK", is_bad_deploy=True, expected_target=_GOOD,
        rationale="Anti-distortion guard: when the newest ready candidate IS the witnessed-good one "
                  "(the common case), the ledger picks exactly what recency picks — no behavior "
                  "change, same heal, target correct either way."),

    # --- v2 floor OVER-ESCALATES: thin-evidence paging -> Phase 3 verifier / better evidence --------
    BenchCase(
        name="low_traffic_blip", category="blip",
        description="A single 5xx in a 4-request window (windowed rate 25%, but 1 error total).",
        world={"revisions": _healthy_pair(), "error_rate": 0.25, "sample": {"errs": 1, "total": 4},
               # v5 5.1 anti-false-fire: a TRANSIENT spike (all errors in ONE window) — pooled it looks
               # elevated but errors in < debounce windows collapses the burn detector to PASS. A blip
               # is the 5xx detector's concern, not a sustained burn.
               "error_windows": [{"errs": 12, "total": 50}] + [{"errs": 0, "total": 50}] * 5,
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


# --- v4 Phase 3: irreversible-deploy guard fixtures — OUTSIDE the scored corpus -------------------
# The guard ships default-OFF (the four committed scorecards run without it), so these two worlds
# are exercised by test_bench's dedicated tests via run_case(reversibility=True/False) instead of
# adding a fifth scorecard mode. One world, one difference: whether the serving revision DECLARED
# the forward-only marker.
def _irreversible_world(marked: bool) -> dict:
    revs = _bad_and_good()
    revs[0] = {**revs[0], "irreversible": marked}   # the serving bad deploy (may) declare a migration
    return {"revisions": revs, "error_rate": 0.9, "sample": {"errs": 18, "total": 20},
            "rollback_clears": True, "logs": [_KEYERROR_TRACE]}


REVERSIBILITY_CASES: list[BenchCase] = [
    BenchCase(
        name="irreversible_marker_blocks_rollback", category="irreversible",
        description="The serving bad deploy DECLARED a forward-only change (schema migration, "
                    "airbag.dev/irreversible=true). Rolling back would put pre-migration code in "
                    "front of the migrated datastore — strictly worse than the outage.",
        world=_irreversible_world(marked=True),
        expected_action="ESCALATE", is_bad_deploy=True,
        rationale="THE GAP EVERY OTHER GATE GREENLIGHTS: the target boots fine, GET probes return "
                  "200 (a synthetic probe can't exercise a mutation), the causal check passes, "
                  "_verify can pass — and every write corrupts. Only the DECLARED contract knows. "
                  "With the guard on: ESCALATE with zero traffic shifted."),
    BenchCase(
        name="irreversible_marker_absent_rolls_back", category="irreversible",
        description="The SAME bad deploy without a declared marker — the guard must be invisible "
                    "(fail-open) and the normal rollback heals.",
        world=_irreversible_world(marked=False),
        expected_action="ROLLBACK", is_bad_deploy=True, expected_target=_GOOD,
        rationale="FAIL-OPEN anti-regression: no declaration → the guard changes nothing; the heal "
                  "is byte-identical to today. (It HONORS a contract; it does NOT detect "
                  "migrations — an undeclared forward-only deploy is invisible to it.)"),
]


def case_by_name(name: str) -> BenchCase:
    for c in CASES + REVERSIBILITY_CASES:
        if c.name == name:
            return c
    raise KeyError(name)
