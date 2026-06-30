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


def test_false_rates_do_not_regress_vs_baseline():
    committed = json.loads(_BASELINE.read_text(encoding="utf-8"))
    card = score(_results())
    assert card.false_rollback_rate.num <= committed["false_rollback_rate"]["num"], \
        "false-rollback COUNT increased vs the committed baseline"
    assert card.false_escalation_rate.num <= committed["false_escalation_rate"]["num"], \
        "false-escalation COUNT increased vs the committed baseline"


# --- 3. MEANING (the pre-registered v2 gaps Phases 1-2 close) -------------------------------------
def test_v2_floor_misses_non_5xx_regressions_recall_gap():
    """Phase 1 target: v2 is 5xx-only, so latency/saturation/slo regressions slip through."""
    card = score(_results())
    assert card.rollback_recall.value is not None and card.rollback_recall.value < 0.6, \
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
