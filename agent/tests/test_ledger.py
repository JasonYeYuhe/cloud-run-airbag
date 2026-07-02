"""The v4 serving-history ledger (Phase 1.1): Airbag stamps revisions it has WITNESSED serving
healthily, so the rollback selector can prefer a proven-good target over the bare "newest ready"
recency proxy. Stamping rules under test:
  * a CONFIDENTLY-healthy no-op run (PASS, or OBSERVE with zero observed 5xx) witnesses the SERVING
    revision;
  * a verified mitigation witnesses the TARGET (after _verify proves recovery) — an UNVERIFIED
    post-rollback shift must never stamp (traffic moving is not evidence, recovery proof is);
  * a flaky sub-threshold window (INCONCLUSIVE with errors) must NOT certify;
  * the map is bounded (WITNESS_MAX, least-recently-witnessed evicted) and the transact mutator is
    idempotent under a Firestore-style retry.
conftest pins the memory store + resets it per test."""
from autosre import config, memory, state_store
from autosre.backends import mock
from autosre.state_machine import run_self_heal


# --- the ledger primitive (memory.witness_serving / witnessed_healthy) -----------------
def test_witness_and_read_back():
    assert memory.witnessed_healthy("svc") == {}                 # cold start: empty map
    memory.witness_serving("svc", "svc-00007-good")
    memory.witness_serving("svc", "svc-00007-good")
    w = memory.witnessed_healthy("svc")
    assert set(w) == {"svc-00007-good"}
    assert w["svc-00007-good"]["count"] == 2                     # re-witness bumps the count
    assert w["svc-00007-good"]["last_witnessed_at"] > 0


def test_witness_rejects_empty_revision():
    assert memory.witness_serving("svc", "") is None
    assert memory.witness_serving("svc", None) is None
    assert memory.witnessed_healthy("svc") == {}


def test_witness_bounded_evicts_least_recently_witnessed(monkeypatch):
    monkeypatch.setattr(config, "WITNESS_MAX", 3)
    for i in range(5):
        memory.witness_serving("svc", f"rev-{i}")
    w = memory.witnessed_healthy("svc")
    assert set(w) == {"rev-2", "rev-3", "rev-4"}                 # oldest two evicted


def test_rewitness_refreshes_recency(monkeypatch):
    monkeypatch.setattr(config, "WITNESS_MAX", 3)
    for name in ("rev-a", "rev-b", "rev-c"):
        memory.witness_serving("svc", name)
    memory.witness_serving("svc", "rev-a")                       # refresh a: b is now oldest
    memory.witness_serving("svc", "rev-d")                       # evicts b, not a
    assert set(memory.witnessed_healthy("svc")) == {"rev-a", "rev-c", "rev-d"}


def test_witness_mutator_idempotent_under_transact_retry(monkeypatch):
    """Firestore may re-run a contended transaction's mutator; only the last run commits. The
    mutator must be pure so a retry can't double-count a single witness."""
    real_transact = state_store.transact

    def retrying(coll, doc_id, mutator):
        mutator(state_store.get(coll, doc_id))     # first attempt: aborted, result discarded
        return real_transact(coll, doc_id, mutator)
    monkeypatch.setattr(state_store, "transact", retrying)
    memory.witness_serving("svc", "rev-a")
    assert memory.witnessed_healthy("svc")["rev-a"]["count"] == 1


def test_witness_does_not_disturb_baseline_or_incidents():
    """The ledger shares the per-service doc with the learned baseline — stamping must not corrupt
    the other fields (and vice versa)."""
    memory.observe_healthy("svc", 0.5)
    memory.witness_serving("svc", "rev-a")
    memory.record_incident("svc", "sig", "mitigated")
    assert memory.baseline_for("svc") > config.STAT_BASELINE_RATE   # EMA intact
    assert memory.summary("svc")["incident_count"] == 1
    assert set(memory.witnessed_healthy("svc")) == {"rev-a"}


# --- wired into the heal ----------------------------------------------------------------
def test_observe_clean_run_stamps_serving_revision():
    """A healthy service (zero observed 5xx) no-ops AND witnesses the revision serving the traffic."""
    mock.reset_target("airbag-target", "r")        # healthy revision serving 100%
    res = run_self_heal("inc-w1", "airbag-target")
    assert res["status"] == "noop"
    w = memory.witnessed_healthy("airbag-target")
    assert set(w) == {"airbag-target-00001-good"}


def test_flaky_observe_does_not_stamp(monkeypatch):
    """INCONCLUSIVE with a NONZERO rate (a flaky sub-threshold window) must not certify the serving
    revision — a revision that erred while serving can't be 'witnessed good'."""
    from autosre import signals
    from autosre.state_machine import _healthy_witness
    monkeypatch.setattr(signals, "detect", lambda *a, **k: {
        "verdict": "INCONCLUSIVE", "reason": "1/20 errs — too few to call", "rate": 0.05})
    mock.reset_target("airbag-target", "r")
    res = run_self_heal("inc-w2", "airbag-target")
    assert res["status"] == "noop"
    assert memory.witnessed_healthy("airbag-target") == {}
    # and the rule itself, both arms:
    assert _healthy_witness({"verdict": "INCONCLUSIVE", "rate": 0.05}, {}) is False
    assert _healthy_witness({"verdict": "INCONCLUSIVE", "rate": 0.0}, {}) is True
    assert _healthy_witness({"verdict": "PASS", "rate": 0.005}, {}) is True
    assert _healthy_witness(None, {"error_rate": 0.0}) is True      # stat gate off: clean window
    assert _healthy_witness(None, {"error_rate": 0.03}) is False    # stat gate off: noisy window


def test_mitigated_heal_stamps_the_verified_target():
    """A verified rollback witnesses the TARGET (recovery proven at 100% traffic) — and never the
    bad revision it rolled away from."""
    mock.reset()                                   # bad revision serving -> heal rolls back
    res = run_self_heal("inc-w3", "airbag-target")
    assert res["status"] == "mitigated"
    w = memory.witnessed_healthy("airbag-target")
    assert "airbag-target-00001-good" in w         # the verified rollback target
    assert "airbag-target-00002-bad" not in w      # the bad revision is never witnessed


def test_verified_but_still_erring_window_does_not_stamp(monkeypatch):
    """Defense in depth (Gemini review): even when _verify passes (a lagged log window + a lucky
    probe on a flaky target), a NONZERO post-rollback 5xx window must veto the ledger stamp — the
    heal still counts as mitigated, but the target is not certified witnessed-healthy."""
    from autosre import state_machine, tools
    monkeypatch.setattr(state_machine, "_verify", lambda *a, **k: True)
    real_qer = tools.query_error_rate
    monkeypatch.setattr(tools, "query_error_rate",
                        lambda *a, **k: {**real_qer(*a, **k), "error_rate": 0.08})
    mock.reset()
    res = run_self_heal("inc-w6", "airbag-target")
    assert res["status"] == "mitigated"                # the heal itself is unchanged
    assert memory.witnessed_healthy("airbag-target") == {}   # but nothing was certified


def test_failed_verify_does_not_stamp(monkeypatch):
    """The never-stamp rule: a rollback whose _verify FAILS (errors persist) escalates WITHOUT
    witnessing the rolled-back-to revision — traffic moving is not evidence of health."""
    from autosre import state_machine
    monkeypatch.setattr(state_machine, "_verify", lambda *a, **k: False)
    mock.reset()
    res = run_self_heal("inc-w4", "airbag-target")
    assert res["status"] == "escalated"
    assert memory.witnessed_healthy("airbag-target") == {}


def test_l1_pass_recheck_stamps_serving(monkeypatch):
    """An L1-gated rollback approved AFTER the service self-recovered: the PASS re-check no-ops and
    witnesses the revision serving at approval time."""
    from autosre import autonomy, signals
    from autosre.state_machine import apply_approval
    autonomy.set_level("airbag-target", "L1")
    mock.reset()                                   # bad serving -> decision gated for approval
    res = run_self_heal("inc-w5", "airbag-target")
    assert res["status"] == "awaiting_approval"
    assert memory.witnessed_healthy("airbag-target") == {}          # gate stamps nothing
    mock.reset_target("airbag-target", "r")        # world self-recovers: good revision serving
    monkeypatch.setattr(signals, "detect", lambda *a, **k: {
        "verdict": "PASS", "reason": "confidently healthy", "rate": 0.0})
    out = apply_approval("inc-w5", approve=True)
    assert out["status"] == "noop"
    assert set(memory.witnessed_healthy("airbag-target")) == {"airbag-target-00001-good"}
