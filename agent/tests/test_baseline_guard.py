"""v5 Phase 5.2 — baseline integrity guard (AIRBAG_BASELINE_GUARD, default OFF). The learned EMA
folds ONLY a CONFIDENT-healthy sample (PASS or zero-error), so a slow burn (INCONCLUSIVE-with-errors
OBSERVE) can't RAISE the baseline it is later measured against (the poison the burn detector 5.1
exists to catch), and per-fold drift is clamped. Flag OFF -> byte-identical v4 fold."""
from autosre import config, memory, state_store, tools
from autosre.state_machine import run_self_heal
from bench.harness import _PINNED, SERVICE, FixtureBackend

# a world the 5xx detector reads INCONCLUSIVE (1/40 straddles the baseline) -> OBSERVE, with an
# ELEVATED-but-sub-threshold rate that a slow burn would poison the baseline with.
_BURNY_OBSERVE = {
    "revisions": [
        {"name": "svc-cur", "ready": True, "traffic_percent": 100, "create_time": "2026-07-02T00:00:00Z"},
        {"name": "svc-prev", "ready": True, "traffic_percent": 0, "create_time": "2026-07-01T00:00:00Z"},
    ],
    "error_rate": 0.03, "sample": {"errs": 1, "total": 40}, "rollback_clears": True,
}


# --- the per-fold drift clamp (memory.observe_healthy) --------------------------------------------
def test_clamp_off_folds_the_full_ema():
    memory.observe_healthy("svc", 0.5)                    # flag off -> unclamped EMA
    b = state_store.get("service_memory", "svc")["baseline_rate"]
    assert abs(b - (0.2 * 0.5 + 0.8 * config.STAT_BASELINE_RATE)) < 1e-9   # 0.116


def test_clamp_on_limits_per_fold_drift(monkeypatch):
    monkeypatch.setattr(config, "BASELINE_GUARD", True)
    monkeypatch.setattr(config, "BASELINE_MAX_FOLD_DRIFT", 0.01)
    memory.observe_healthy("svc", 0.5)                    # would jump to 0.116 unclamped
    b = state_store.get("service_memory", "svc")["baseline_rate"]
    assert abs(b - (config.STAT_BASELINE_RATE + 0.01)) < 1e-9   # clamped to prev + drift = 0.03


# --- the fold-gating (state_machine OBSERVE branch) -----------------------------------------------
def _observe_then_baseline(guard: bool, monkeypatch) -> float:
    for k, v in _PINNED.items():
        monkeypatch.setattr(config, k, v)
    monkeypatch.setattr(config, "SIGNALS", "5xx")
    monkeypatch.setattr(config, "BASELINE_GUARD", guard)
    monkeypatch.setattr(tools, "get_backend", lambda: FixtureBackend(dict(_BURNY_OBSERVE)))
    state_store.reset_memory()
    before = memory.baseline_for(SERVICE)
    assert run_self_heal("burny", SERVICE)["status"] == "noop"   # INCONCLUSIVE 5xx -> OBSERVE
    return before, memory.baseline_for(SERVICE)


def test_guard_off_a_slow_burn_observe_poisons_the_baseline(monkeypatch):
    before, after = _observe_then_baseline(guard=False, monkeypatch=monkeypatch)
    assert after > before   # v4: the INCONCLUSIVE-with-errors OBSERVE folds 2.5% -> baseline RISES (the poison)


def test_guard_on_a_slow_burn_observe_does_not_fold(monkeypatch):
    before, after = _observe_then_baseline(guard=True, monkeypatch=monkeypatch)
    assert after == before  # v5 5.2: a non-confident-healthy OBSERVE does NOT fold -> baseline protected


def test_guard_on_still_folds_a_confidently_healthy_observe(monkeypatch):
    """A genuinely-healthy (zero-error) OBSERVE MUST still fold — the guard only skips burn-y samples."""
    clean = dict(_BURNY_OBSERVE, sample={"errs": 0, "total": 20}, error_rate=0.0)
    for k, v in _PINNED.items():
        monkeypatch.setattr(config, k, v)
    monkeypatch.setattr(config, "SIGNALS", "5xx")
    monkeypatch.setattr(config, "BASELINE_GUARD", True)
    monkeypatch.setattr(tools, "get_backend", lambda: FixtureBackend(clean))
    state_store.reset_memory()
    before = memory.baseline_for(SERVICE)
    assert run_self_heal("clean", SERVICE)["status"] == "noop"
    assert memory.baseline_for(SERVICE) < before   # a zero-error sample folds DOWN toward the floor
