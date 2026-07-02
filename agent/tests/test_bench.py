"""Airbag-Bench regression tests (Phase 0.1).

Three jobs:
  1. SMOKE — the harness replays every case, and the two load-bearing wirings hold (a clean crash
     reaches status=='mitigated'; the coincident-dependency case DECIDES rollback yet ends escalated).
  2. GOLDEN RATCHET — per-case decided actions match the committed baseline_scorecard.json, and the
     false-rollback / false-escalation COUNTS never increase vs the committed baseline. Any change is
     therefore intentional + reviewed (a real TDD loop for Phases 1-2), and a recall fix can't
     silently regress precision on another case.
  3. MEANING — the v2 floor's KNOWN gaps are asserted, so the baseline's interpretation is guarded,
     not just a JSON blob. Phases 1-2 flip these.
"""
from __future__ import annotations

import json
from pathlib import Path

from bench.fixtures import CASES
from bench.harness import run_bench
from bench.scorecard import score

_BASELINE = Path(__file__).resolve().parent / "bench" / "baseline_scorecard.json"


def _results():
    return run_bench()


# --- 1. SMOKE -------------------------------------------------------------------------------------
def test_every_case_decides_a_valid_action():
    results = _results()
    assert len(results) == len(CASES)
    for r in results:
        assert r.decided_action in ("ROLLBACK", "OBSERVE", "ESCALATE"), r


def test_clean_crash_reaches_mitigated():
    """Guards the harness wiring bug the design review caught: a fresh FixtureBackend per
    get_backend() call would lose post-rollback state and escalate EVERY rollback case. A single
    instance per case must let the crash actually mitigate."""
    r = next(x for x in _results() if x.name == "crash_total_outage")
    assert r.decided_action == "ROLLBACK"
    assert r.status == "mitigated", f"crash should mitigate (wiring regression?), got {r.status}"
    assert r.stages > 0


def test_dependency_outage_decides_rollback_but_does_not_mitigate():
    """Guards the DECISION-vs-terminal keying: v2 DECIDES rollback (the precision defect we measure),
    but the rollback does not clear the dependency outage, so it ends escalated/wasted. If a metric
    keyed off terminal status, this false-rollback would vanish and v2 would look correct."""
    r = next(x for x in _results() if x.name == "coincident_dependency_outage")
    assert r.decided_action == "ROLLBACK"
    assert r.status == "escalated"
    assert r.cleared is False


# --- 2. GOLDEN RATCHET ----------------------------------------------------------------------------
def test_per_case_matches_committed_baseline():
    committed = json.loads(_BASELINE.read_text(encoding="utf-8"))
    golden = {c["name"]: c["decided"] for c in committed["per_case"]}
    for r in _results():
        assert r.decided_action == golden[r.name], (
            f"{r.name}: decided {r.decided_action} but committed baseline is {golden[r.name]}. "
            "If this change is intentional (a Phase 1/2 improvement), re-run "
            "`python tests/bench/run_bench.py --write` and review the diff.")


def test_default_signals_reproduce_committed_baseline():
    """Phase 1: the default AIRBAG_SIGNALS=5xx path must reproduce the committed scorecard exactly —
    the multi-signal engine is a no-op refactor until more detectors are enabled."""
    committed = json.loads(_BASELINE.read_text(encoding="utf-8"))
    card = score(run_bench(signals="5xx"))
    got = card.to_dict()
    for metric in ("rollback_precision", "rollback_recall", "false_rollback_rate",
                   "false_escalation_rate", "accuracy", "target_correctness"):
        assert got[metric] == committed[metric], f"{metric} drifted vs committed baseline"
    assert {c["name"]: c["decided"] for c in got["per_case"]} == \
        {c["name"]: c["decided"] for c in committed["per_case"]}


def test_false_rates_do_not_regress_vs_baseline():
    committed = json.loads(_BASELINE.read_text(encoding="utf-8"))
    card = score(_results())
    assert card.false_rollback_rate.num <= committed["false_rollback_rate"]["num"], \
        "false-rollback COUNT increased vs the committed baseline"
    assert card.false_escalation_rate.num <= committed["false_escalation_rate"]["num"], \
        "false-escalation COUNT increased vs the committed baseline"


# --- 3. MEANING (the pre-registered v2 gaps Phases 1-2 close) -------------------------------------
def test_v2_floor_misses_non_5xx_regressions_recall_gap():
    """Phase 1 target: v2 is 5xx-only, so latency/saturation/slo regressions slip through.
    (Threshold recalibrated for the v4 bad_bad cases — 3 new 5xx-catchable ROLLBACK worlds lift the
    5xx floor to 7/11 ≈ 0.64; the per-miss asserts below are the real content.)"""
    card = score(_results())
    assert card.rollback_recall.value is not None and card.rollback_recall.value < 0.7, \
        "recall unexpectedly high — has the multi-signal detector landed? update the baseline"
    by_name = {r.name: r for r in _results()}
    for miss in ("latency_regression", "saturation_cpu", "slo_slow_burn"):
        assert by_name[miss].decided_action == "OBSERVE", f"{miss} should be a v2 MISS"


def test_v2_floor_wrongly_rolls_back_dependency_outage_precision_gap():
    """Phase 2 target: no causal grounding, so a coincident dependency outage is wrongly rolled back."""
    card = score(_results())
    assert card.rollback_precision.value is not None and card.rollback_precision.value < 1.0, \
        "precision unexpectedly perfect — has causal grounding landed? update the baseline"
    assert card.false_rollback_rate.num >= 1


def test_v2_floor_catches_the_clear_crashes():
    by_name = {r.name: r for r in _results()}
    assert by_name["crash_total_outage"].decided_action == "ROLLBACK"
    assert by_name["crash_high_5xx"].decided_action == "ROLLBACK"


# --- 4. MULTI-SIGNAL (Phase 1.2): the latency detector lifts recall WITHOUT regressing precision ---
def test_multisignal_lifts_recall_without_regressing_precision():
    """The core Phase 1 proof: enabling latency catches the out-of-window latency regressions the
    5xx-only floor misses, and adds no false rollback from DETECTOR noise (the §6 guard —
    latency_within_slo / latency_spike_transient stay OBSERVE). v4 recalibration: the ONE extra
    false rollback multisignal may show is `latency_coincident_slow_target` — a CORRECT detection
    whose rollback is futile for causal reasons the detector can't see (an external slow
    dependency); it is invisible to 5xx-only mode, and the causal latency-veto closes it
    (asserted below in test_causal_precheck_eliminates_false_rollbacks)."""
    base = score(run_bench(signals="5xx"))
    multi_results = run_bench(signals="5xx,latency")
    multi = score(multi_results)
    assert multi.rollback_recall.value > base.rollback_recall.value, "latency detector must lift recall"
    assert multi.rollback_recall.num >= 4                      # catches both latency regressions
    new_false = ({r.name for r in multi_results if r.final_action == "ROLLBACK"
                  and r.expected_action != "ROLLBACK"}
                 - {r.name for r in run_bench(signals="5xx") if r.final_action == "ROLLBACK"
                    and r.expected_action != "ROLLBACK"})
    assert new_false <= {"latency_coincident_slow_target"}, \
        f"multisignal added unexpected false rollbacks: {new_false}"


def test_multisignal_rolls_back_latency_regressions():
    by_name = {r.name: r for r in run_bench(signals="5xx,latency")}
    assert by_name["latency_regression"].decided_action == "ROLLBACK"
    assert by_name["latency_regression_moderate"].decided_action == "ROLLBACK"


def test_multisignal_debounce_suppresses_transient_spike():
    """A momentary latency spike (one hot window) must NOT roll back — the debounce is load-bearing;
    a naive non-debounced detector would false-rollback here."""
    by_name = {r.name: r for r in run_bench(signals="5xx,latency")}
    assert by_name["latency_spike_transient"].decided_action == "OBSERVE"
    assert by_name["latency_within_slo"].decided_action == "OBSERVE"


def test_multisignal_matches_committed_scorecard():
    committed = json.loads((_BASELINE.parent / "multisignal_scorecard.json").read_text(encoding="utf-8"))
    golden = {c["name"]: c["decided"] for c in committed["per_case"]}
    for r in run_bench(signals="5xx,latency"):
        assert r.decided_action == golden[r.name], (
            f"{r.name}: {r.decided_action} vs committed {golden[r.name]} — re-run "
            "`python tests/bench/run_bench.py --signals 5xx,latency --write` and review the diff.")


# --- 5. CAUSAL PRE-CHECK (Phase 2a): precision up, false-rollback→0, ZERO legit rollbacks blocked ---
def test_causal_precheck_eliminates_false_rollbacks():
    """The core Phase 2a proof: probing the rollback target before committing turns both coincident
    external-cause outages into ESCALATE (no wasted traffic shift) → precision up, false-rollback →0."""
    for sig in ("5xx", "5xx,latency"):
        base = score(run_bench(signals=sig, causal=False))
        caus = score(run_bench(signals=sig, causal=True))
        assert caus.false_rollback_rate.num < base.false_rollback_rate.num, "must drop false rollbacks"
        assert caus.false_rollback_rate.num == 0, "both coincident cases should stop rolling back"
        assert caus.rollback_precision.value > base.rollback_precision.value


def test_causal_never_blocks_a_rollback_aimed_at_a_healthy_target():
    """SAFETY — the worst failure is protecting a bad revision. For every case whose causal-OFF
    rollback AIMED at a target the world models as healthy (probe below the confidence bar), the
    causal check must still roll back. (v4 recalibration: a veto of a rollback aimed at a
    CONFIDENTLY-degraded target — bad_bad_cold_ledger's landmine — is the check working as
    designed, so plain recall equality no longer holds on this corpus; the healthy-AIMED subset is
    the real invariant. The bad→bad escalate-vs-heal contrast is pinned in section 6.)"""
    from bench.fixtures import case_by_name
    causal_cards = {"5xx": "causal_5xx_scorecard.json", "5xx,latency": "causal_scorecard.json"}
    for sig in ("5xx", "5xx,latency"):
        base = {r.name: r for r in run_bench(signals=sig, causal=False)}
        caus_results = run_bench(signals=sig, causal=True)
        caus = {r.name: r for r in caus_results}
        for name, b in base.items():
            if not (b.rolled_back and b.expected_action == "ROLLBACK"):
                continue
            world = case_by_name(name).world
            probe = (world.get("target_probes", {}).get(b.chosen_target)
                     or world.get("target_probe"))
            if _probe_unhealthy(probe, world):
                continue   # aimed at a confidently-degraded target (on an axis the veto can see
                           # for THIS incident) — a veto is correct, not a block
            assert caus[name].rolled_back, f"causal check blocked a healthy-aimed rollback: {name}"
        # RATCHET (Gemini review): the subset loop alone would let an aim-everything-at-broken-
        # targets regression hide (every case would be exempted, recall would tank silently). The
        # committed causal scorecard pins the mitigation floor — recall can never drop below it.
        committed = json.loads((_BASELINE.parent / causal_cards[sig]).read_text(encoding="utf-8"))
        got = score(caus_results)
        assert got.rollback_recall.num >= committed["rollback_recall"]["num"], \
            f"causal-mode recall dropped below the committed floor ({sig})"
    by_name = {r.name: r for r in run_bench(signals="5xx,latency", causal=True)}
    assert by_name["masquerade_real_bad_deploy"].rolled_back is True   # healthy target -> proceed
    assert by_name["intermittent_target"].rolled_back is True          # flaky target -> proceed
    assert by_name["coincident_dependency_outage"].rolled_back is False  # target also down -> escalate
    assert by_name["coincident_quota_exhaustion"].rolled_back is False
    assert by_name["bad_bad_ledger_heals"].rolled_back is True    # the ledger's aim passes the probe


def test_causal_off_reproduces_committed_scorecards():
    """With the causal check OFF (the default), the 5xx and multisignal scorecards are byte-identical
    to the committed baselines — a wiring slip that silently enabled it would be caught here."""
    for sig, fname in (("5xx", "baseline_scorecard.json"), ("5xx,latency", "multisignal_scorecard.json")):
        committed = json.loads((_BASELINE.parent / fname).read_text(encoding="utf-8"))
        got = score(run_bench(signals=sig, causal=False)).to_dict()
        assert {c["name"]: c["final"] for c in got["per_case"]} == \
            {c["name"]: c["final"] for c in committed["per_case"]}


def test_causal_matches_committed_scorecard():
    committed = json.loads((_BASELINE.parent / "causal_scorecard.json").read_text(encoding="utf-8"))
    golden = {c["name"]: c["final"] for c in committed["per_case"]}
    for r in run_bench(signals="5xx,latency", causal=True):
        assert r.final_action == golden[r.name], (
            f"{r.name}: {r.final_action} vs committed {golden[r.name]} — re-run "
            "`python tests/bench/run_bench.py --signals 5xx,latency --causal --write` and review.")


def _probe_unhealthy(probe: dict | None, world: dict | None = None) -> bool:
    """A probe result models a confidently-degraded target on an axis the causal check can actually
    SEE for this world's incident: errs (the 5xx gate runs for every incident) always counts;
    slow counts ONLY when the world models a latency incident (has latency_windows) — the latency
    gate is keyed on primary_signal, so a slow-only probe on a 5xx-driven world would 'certify' a
    coupling the veto structurally cannot enforce (review finding)."""
    if probe is None:
        return False
    if int(probe.get("errs", 0)) >= 3:
        return True
    latency_world = bool((world or {}).get("latency_windows"))
    return latency_world and int(probe.get("slow", 0)) >= 3


def test_fixture_coupling_dependency_target_is_unhealthy():
    """Anti-gaming: a case that models an external cause (rollback_clears==False) MUST carry a
    confidently-unhealthy target_probe (on the 5xx OR latency axis), and vice-versa — so
    target_probe can't be tuned to flip one case. (Bad-deploy cases either omit target_probe
    [healthy default] or set a below-the-bar one.)"""
    from bench.fixtures import CASES
    for c in CASES:
        clears = c.world.get("rollback_clears", True)
        probe = c.world.get("target_probe")
        if "clears_on" in c.world:
            continue   # per-revision worlds: the coupling is enforced per-revision below
        if not clears:
            assert _probe_unhealthy(probe, c.world), \
                f"{c.name}: rollback_clears=False must have a confidently-unhealthy target_probe"
        if probe is not None and clears:
            assert not _probe_unhealthy(probe, c.world), \
                f"{c.name}: a clearing rollback must have a healthy/below-the-bar target_probe"


def test_fixture_coupling_per_revision_worlds():
    """Anti-gaming for the v4 per-revision (bad→bad) worlds: a revision the rollback CLEARS on must
    probe healthy, and a non-clearing candidate (the landmine) must probe confidently unhealthy —
    the probe model and the outcome model can't be tuned independently to flip a case."""
    from bench.fixtures import CASES
    for c in CASES:
        clears_on = c.world.get("clears_on")
        if clears_on is None:
            continue
        probes = c.world.get("target_probes", {})
        for rev, p in probes.items():
            if _probe_unhealthy(p, c.world):
                assert rev not in clears_on, \
                    f"{c.name}: {rev} probes unhealthy yet the rollback clears on it"
        for rev in clears_on:
            assert not _probe_unhealthy(probes.get(rev), c.world), \
                f"{c.name}: clears_on revision {rev} must not carry an unhealthy probe"
        # every 0-traffic ready candidate NOT in clears_on is a landmine: it must probe unhealthy,
        # or the world would let a wrong-target rollback look successful.
        for r in c.world["revisions"]:
            if r.get("traffic_percent", 0) == 0 and r.get("ready") and r["name"] not in clears_on:
                assert _probe_unhealthy(probes.get(r["name"]), c.world), \
                    f"{c.name}: non-clearing candidate {r['name']} must probe confidently unhealthy"


# --- 5b. LATENCY-AXIS CAUSAL VETO (v4 Phase 2): the probe matches the incident's signal ----------
def test_latency_veto_escalates_slow_target_without_shifting():
    """The v4 Phase 2 proof: for a LATENCY incident whose rollback target is 200-but-confidently-
    slow (an external slow dependency), the latency-keyed probe returns COINCIDENT and Airbag
    escalates with ZERO traffic shifted — where causal-off ships the futile rollback (wasted)."""
    off = next(r for r in run_bench(signals="5xx,latency", causal=False)
               if r.name == "latency_coincident_slow_target")
    assert off.rolled_back and off.status == "escalated" and off.cleared is False   # wasted shift
    on = next(r for r in run_bench(signals="5xx,latency", causal=True)
              if r.name == "latency_coincident_slow_target")
    assert on.final_action == "ESCALATE" and not on.rolled_back                     # vetoed pre-shift


def test_latency_veto_never_blocks_a_fast_or_warming_target():
    """SAFETY: the latency axis must not protect a genuinely-slow revision — a fast target and a
    below-the-bar warmup blip both PROCEED and the latency bad-deploys still heal."""
    by_name = {r.name: r for r in run_bench(signals="5xx,latency", causal=True)}
    for name in ("latency_regression", "latency_regression_moderate", "latency_target_warmup_blip"):
        assert by_name[name].rolled_back is True, f"latency veto wrongly blocked {name}"
        assert by_name[name].status == "mitigated"


# --- 6. TARGET-CORRECTNESS (v4 Phase 1): the ledger aims at witnessed-good; recency can't --------
_BB_GOOD = "airbag-target-00009-good"
_BB_LANDMINE = "airbag-target-00011-landmine"


def test_ledger_aims_at_witnessed_good_in_every_mode():
    """The marquee positive: with a witnessed history, the rollback is aimed at the proven-good
    OLDER revision (not the newer landmine) and the heal completes — in the 5xx floor, multisignal,
    and causal configurations alike."""
    for kw in ({}, {"signals": "5xx,latency"}, {"signals": "5xx,latency", "causal": True}):
        warm = next(r for r in run_bench(**kw) if r.name == "bad_bad_ledger_heals")
        assert warm.chosen_target == _BB_GOOD and warm.target_source == "ledger"
        assert warm.final_action == "ROLLBACK" and warm.status == "mitigated"


def test_cold_ledger_recency_aims_at_the_landmine():
    """The matched control (the committed OLD-behavior proof): cold start = recency aims at the
    landmine. Causal OFF: the rollback is WASTED (shift, fail verify, escalate). Causal ON: the
    live probe vetoes pre-shift and a human is paged even though a proven-good revision exists —
    exactly the ESCALATE the ledger turns into an autonomous heal."""
    cold = next(r for r in run_bench() if r.name == "bad_bad_cold_ledger")
    assert cold.chosen_target == _BB_LANDMINE and cold.target_source == "recency"
    assert cold.rolled_back and cold.status == "escalated" and cold.cleared is False   # wasted
    cold = next(r for r in run_bench(signals="5xx,latency", causal=True)
                if r.name == "bad_bad_cold_ledger")
    assert cold.final_action == "ESCALATE" and not cold.rolled_back   # vetoed pre-shift, human paged


def test_negative_control_ledger_agrees_with_recency():
    """Anti-distortion: when the newest ready candidate IS the witnessed one (the common case), the
    ledger picks exactly what recency would — same target, same heal."""
    ctl = next(r for r in run_bench() if r.name == "bad_deploy_newest_is_witnessed")
    assert ctl.chosen_target == "airbag-target-00001-good" and ctl.status == "mitigated"


def test_target_correctness_metric_scores_the_aim():
    """The metric is DECIDED-keyed — it scores the SELECTOR's aim, not the safety net downstream:
    the cold bad→bad control aims at the landmine and is the ONE wrong aim in EVERY mode, whether
    that aim then shipped (causal off: the wasted rollback) or was vetoed pre-shift (causal on —
    an executed-keyed metric would have read a flattering 100% there, per the Gemini review)."""
    for kw in ({}, {"signals": "5xx,latency", "causal": True}):
        card = score(run_bench(**kw))
        assert card.target_correctness.den >= 7
        assert card.target_correctness.num == card.target_correctness.den - 1
        wrong = [c for c in card.per_case
                 if c["decided"] == "ROLLBACK" and c["expected_target"]
                 and c["chosen_target"] != c["expected_target"]]
        assert [c["name"] for c in wrong] == ["bad_bad_cold_ledger"]   # exactly the cold control
