"""v5 Phase 5.1 — pooled-Wilson SLO burn-rate detector. A slow error-budget burn is sub-threshold in
any SINGLE window (single-window Wilson LB of 1/40 ≈ 0.45% never clears the baseline) but POOLED over
the windows the LB tightens and clears it — closing the pre-registered slo_slow_burn bench miss. An
all-in-one-window SPIKE collapses to PASS (that's the 5xx detector's job). Opt-in via AIRBAG_SIGNALS.
"""
import json
from pathlib import Path

from autosre import analyzer, config
from autosre.signals.engine import SignalContext, _detect_burn

_DIR = Path(__file__).resolve().parent / "bench"


def _ctx(error_windows, baseline=0.02):
    c = SignalContext(service="s", region="r", baseline_rate=baseline)
    c.error_windows = error_windows
    return c


# --- the detector math ----------------------------------------------------------------------------
def test_pooled_sustained_burn_fails_where_single_window_is_inconclusive():
    # each window 2/50 is individually INCONCLUSIVE; pooled 12/300 is a confident burn
    assert analyzer.analyze(2, 50, 0.02)["verdict"] == "INCONCLUSIVE"
    r = _detect_burn(_ctx([{"errs": 2, "total": 50}] * 6))
    assert r["verdict"] == "FAIL" and r["detail"]["ci_low"] > 0.02
    assert r["detail"]["windows_with_errors"] == 6


def test_spike_in_one_window_collapses_to_pass():
    """Same pooled rate but errors concentrated in ONE window = a spike, not a burn -> PASS (debounce)."""
    r = _detect_burn(_ctx([{"errs": 12, "total": 50}] + [{"errs": 0, "total": 50}] * 5))
    assert r["verdict"] == "PASS" and r["detail"]["windows_with_errors"] == 1


def test_pooled_below_baseline_is_pass():
    r = _detect_burn(_ctx([{"errs": 0, "total": 50}] * 5 + [{"errs": 1, "total": 50}]))
    assert r["verdict"] == "PASS"


def test_no_data_is_inconclusive():
    assert _detect_burn(_ctx([]))["verdict"] == "INCONCLUSIVE"
    assert _detect_burn(_ctx([{"errs": 0, "total": 0}]))["verdict"] == "INCONCLUSIVE"


def test_min_pooled_errors_gate_blocks_a_tiny_burn():
    """Below BURN_MIN_ERRORS pooled errors -> INCONCLUSIVE/PASS even if a few windows have 1 error."""
    r = _detect_burn(_ctx([{"errs": 1, "total": 50}] * 3 + [{"errs": 0, "total": 50}] * 3))
    assert r["verdict"] == "PASS"   # 3 pooled errors < BURN_MIN_ERRORS(5) -> not a confident burn


# --- integration through the real seam (bench harness) --------------------------------------------
def test_burn_closes_the_slo_slow_burn_miss():
    from bench.harness import run_case
    from bench.fixtures import case_by_name
    c = case_by_name("slo_slow_burn")
    assert run_case(c, signals="5xx").decided_action == "OBSERVE"          # v2/5xx floor MISSES it
    hit = run_case(c, signals="5xx,burn")
    assert hit.decided_action == "ROLLBACK" and hit.status == "mitigated"  # burn CLOSES the miss
    assert hit.chosen_target == "airbag-target-00001-good"


def test_burn_does_not_fire_on_benign_sustained_noise():
    """Anti-false-fire (v5 review): a benign service SUSTAINED above the baseline (healthy_noisy, ~3%
    vs a 2% baseline) must NOT fire burn — the pooled Wilson LB (~1.6%) doesn't confidently clear the
    baseline. Burn catches a CONFIDENT elevation, not benign noise above the floor."""
    from bench.fixtures import case_by_name
    from bench.harness import run_case
    r = run_case(case_by_name("healthy_noisy"), signals="5xx,burn")
    assert r.decided_action == "OBSERVE" and not r.rolled_back


def test_burn_adds_no_false_rollbacks_corpus_wide():
    """Enabling burn must flip ONLY slo_slow_burn — a detector that false-fired elsewhere is a bug.
    NON-VACUOUS (v5 review): the benign cases now carry REALISTIC error_windows consistent with their
    declared rates (healthy_noisy = sustained 3%, low_traffic_blip = a transient spike), so this
    actually exercises the false-fire surface rather than feeding a clean zero signal."""
    from bench.harness import run_bench
    base = {r.name: r.final_action for r in run_bench(signals="5xx,latency")}
    allm = {r.name: r.final_action for r in run_bench(signals="all")}
    flipped = {n: (base[n], allm[n]) for n in base if base[n] != allm[n]}
    assert flipped == {"slo_slow_burn": ("OBSERVE", "ROLLBACK")}


def test_all_mode_matches_committed_scorecard():
    from bench.harness import run_bench
    committed = json.loads((_DIR / "all_scorecard.json").read_text(encoding="utf-8"))
    golden = {c["name"]: c["final"] for c in committed["per_case"]}
    for r in run_bench(signals="all"):
        assert r.final_action == golden[r.name], (
            f"{r.name}: {r.final_action} vs committed {golden[r.name]} — re-run "
            "`python tests/bench/run_bench.py --signals all --write` and review the diff.")


def test_default_5xx_makes_no_burn_backend_call(monkeypatch):
    """Byte-identical: with the default 5xx signal, the burn collector is never called (no extra probes)."""
    from autosre import tools
    called = []
    monkeypatch.setattr(tools, "sample_error_windows", lambda *a, **k: called.append(1) or [])
    monkeypatch.setattr(config, "SIGNALS", "5xx")
    from autosre import signals
    signals.detect("svc", "r", 0.02)
    assert called == []
