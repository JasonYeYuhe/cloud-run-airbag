"""Cross-incident memory + the learned per-service baseline. conftest pins the memory store +
resets it per test."""
from autosre import config, memory
from autosre.backends import mock
from autosre.state_machine import run_self_heal


# --- learned baseline ----------------------------------------------------------------
def test_baseline_defaults_then_learns():
    assert memory.baseline_for("svc") == config.STAT_BASELINE_RATE   # no history -> config default
    memory.observe_healthy("svc", 0.5)
    b1 = memory.baseline_for("svc")
    assert config.STAT_BASELINE_RATE < b1 < 0.5     # EMA climbs from the default toward the sample
    memory.observe_healthy("svc", 0.0)
    assert config.STAT_BASELINE_FLOOR <= memory.baseline_for("svc") < b1   # then decays back down


def test_baseline_is_floored():
    for _ in range(20):
        memory.observe_healthy("svc", 0.0)          # EMA -> ~0; read clamps to the floor
    assert memory.baseline_for("svc") == config.STAT_BASELINE_FLOOR


# --- incident memory + recurrence ----------------------------------------------------
def test_record_and_recurrence_window():
    for _ in range(3):
        memory.record_incident("svc", "5xx:/api/orders", "mitigated", "rev-good")
    assert memory.recurrence("svc", "5xx:/api/orders") == 3
    assert memory.recurrence("svc", "other") == 0
    assert memory.recurrence("svc", "5xx:/api/orders", window_s=0) == 0  # all are older than 0s


def test_recent_history_is_bounded(monkeypatch):
    monkeypatch.setattr(config, "MEMORY_RECENT_MAX", 5)
    for i in range(9):
        memory.record_incident("svc", f"sig{i}", "mitigated")
    s = memory.summary("svc")
    assert s["incident_count"] == 9 and len(s["recent"]) == 5  # count grows, history is capped


# --- wired into the heal -------------------------------------------------------------
def test_rollback_heal_records_incident_without_collapsing_baseline():
    mock.reset()
    config.GEMINI_API_KEY = ""
    res = run_self_heal("inc-mem", "airbag-target")
    assert res["status"] == "mitigated"
    s = memory.summary("airbag-target")
    assert s["incident_count"] >= 1 and s["recent"][-1]["status"] == "mitigated"
    # we must NOT fold the momentary post-recovery 0.0 -> the baseline stays at the default,
    # not collapsed to the floor (the bug the review caught).
    assert s["baseline_rate"] == config.STAT_BASELINE_RATE
    assert s["baseline_samples"] == 0  # a ROLLBACK heal teaches no steady-state baseline sample


def test_observe_decision_teaches_baseline():
    mock.reset_target("airbag-target", "r")  # healthy serving -> OBSERVE (no rollback)
    config.GEMINI_API_KEY = ""
    res = run_self_heal("inc-obs", "airbag-target")
    assert res["status"] == "noop"
    assert memory.summary("airbag-target")["baseline_samples"] >= 1  # OBSERVE folded a sample


def test_l1_records_a_single_incident_not_two():
    from autosre import autonomy
    from autosre.state_machine import apply_approval
    mock.reset()
    config.GEMINI_API_KEY = ""
    autonomy.set_level("airbag-target", "L1")
    run_self_heal("inc-l1m", "airbag-target")          # gated — must NOT record yet
    assert memory.summary("airbag-target")["incident_count"] == 0
    apply_approval("inc-l1m", approve=True)            # resolves -> exactly one record
    assert memory.summary("airbag-target")["incident_count"] == 1
