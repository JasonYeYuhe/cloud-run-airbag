"""v5 Phase 3.1 — witness-freshness horizon + blind-landing visibility (AIRBAG_TARGET_EVIDENCE).

A causal PROBE-ERROR against an UNWITNESSED rollback target means Airbag would land traffic with ZERO
positive evidence — exactly the storm step that put traffic on the bug revision. Behind
AIRBAG_TARGET_EVIDENCE (a NO-OP unless AIRBAG_CAUSAL_CHECK is also on) Airbag makes ONE bounded probe
retry then PROCEEDS fail-open with a first-class blind_landing marker — MEASURED, never blocked (the
locked v3 "never block a legit rollback" posture). Plus: a witness older than WITNESS_FRESH_S is cold.
Driven through the REAL run_self_heal seam against the bench FixtureBackend (probe-call counting).
"""
from autosre import config, incidents, memory, state_store, tools
from autosre.state_machine import _witnessed_for_selection, run_self_heal
from bench.harness import _PINNED, SERVICE, FixtureBackend

_PROBE_ERROR = {"errs": 0, "total": 0, "slow": 0}   # total<=0 -> causal INCONCLUSIVE + probe_errored


class _CountingBackend(FixtureBackend):
    def __init__(self, world):
        super().__init__(world)
        self.probe_calls = 0

    def probe_revision_health(self, service, region, revision, n=8):
        self.probe_calls += 1
        return super().probe_revision_health(service, region, revision, n)


def _world(target_probe):
    return {
        "revisions": [
            {"name": "svc-bad", "ready": True, "traffic_percent": 100, "create_time": "2026-07-02T00:00:00Z"},
            {"name": "svc-good", "ready": True, "traffic_percent": 0, "create_time": "2026-07-01T00:00:00Z"},
        ],
        "error_rate": 1.0, "sample": {"errs": 8, "total": 8},   # confident 5xx FAIL -> rollback decided
        "target_probe": target_probe, "rollback_clears": True,  # target is actually good -> mitigates
    }


def _run(world, *, target_evidence, causal=True, witness=(), stale=False):
    keys = list(_PINNED) + ["CAUSAL_CHECK_ENABLED", "TARGET_EVIDENCE", "WITNESS_FRESH_S"]
    saved = {k: getattr(config, k) for k in keys}
    saved_gb = tools.get_backend
    fb = _CountingBackend(world)
    try:
        for k, v in _PINNED.items():
            setattr(config, k, v)
        config.CAUSAL_CHECK_ENABLED = causal
        config.TARGET_EVIDENCE = target_evidence
        tools.get_backend = lambda: fb
        state_store.reset_memory()
        for rev in witness:
            memory.witness_serving(SERVICE, rev)
        if stale:
            config.WITNESS_FRESH_S = 0.0   # every witness is now older than the (zero) horizon
        res = run_self_heal("blind-test", SERVICE)
    finally:
        tools.get_backend = saved_gb
        for k, v in saved.items():
            setattr(config, k, v)
    return res, fb


# --- witness-freshness horizon --------------------------------------------------------------------
def test_witnessed_for_selection_drops_stale(monkeypatch):
    memory.witness_serving("svc", "rev-fresh")
    monkeypatch.setattr(config, "TARGET_EVIDENCE", True)
    monkeypatch.setattr(config, "WITNESS_FRESH_S", 7 * 24 * 3600)
    assert "rev-fresh" in _witnessed_for_selection("svc")        # fresh -> kept
    monkeypatch.setattr(config, "WITNESS_FRESH_S", 0.0)
    assert "rev-fresh" not in _witnessed_for_selection("svc")    # stale -> dropped (cold)
    monkeypatch.setattr(config, "TARGET_EVIDENCE", False)
    assert "rev-fresh" in _witnessed_for_selection("svc")        # flag off -> full v4 map (byte-identical)


# --- blind-landing visibility ---------------------------------------------------------------------
def test_blind_landing_marked_on_probe_error_unwitnessed():
    """probe-error + UNWITNESSED target: ONE retry, then PROCEED with the first-class marker."""
    res, fb = _run(_world(_PROBE_ERROR), target_evidence=True, causal=True)
    assert res["status"] == "mitigated"                          # never blocked — the rollback proceeds
    assert incidents.get("blind-test").get("blind_landing") is True
    assert "BLIND_LANDING" in [e.get("stage") for e in res["events"]]
    assert fb.probe_calls == 2                                   # initial probe + ONE bounded retry


def test_no_blind_landing_when_target_freshly_witnessed():
    res, fb = _run(_world(_PROBE_ERROR), target_evidence=True, causal=True, witness=("svc-good",))
    assert res["status"] == "mitigated"
    assert incidents.get("blind-test").get("blind_landing") is None   # we HAVE evidence -> not blind
    assert fb.probe_calls == 1                                        # no retry


def test_stale_witness_falls_back_to_blind_landing():
    """A STALE witness is treated as unwitnessed -> the probe-error landing is blind (measured)."""
    res, fb = _run(_world(_PROBE_ERROR), target_evidence=True, causal=True, witness=("svc-good",), stale=True)
    assert res["status"] == "mitigated"
    assert incidents.get("blind-test").get("blind_landing") is True
    assert fb.probe_calls == 2


def test_flag_off_is_byte_identical_no_marker():
    res, fb = _run(_world(_PROBE_ERROR), target_evidence=False, causal=True)
    assert res["status"] == "mitigated"
    assert incidents.get("blind-test").get("blind_landing") is None   # no 3.1 behavior when flag off
    assert fb.probe_calls == 1                                        # no retry


def test_no_op_unless_causal_on():
    """AIRBAG_TARGET_EVIDENCE is documented as a no-op unless the causal check is also on."""
    res, fb = _run(_world(_PROBE_ERROR), target_evidence=True, causal=False)
    assert res["status"] == "mitigated"
    assert incidents.get("blind-test").get("blind_landing") is None
    assert fb.probe_calls == 0                                        # no causal probe at all


def test_healthy_probe_never_blind_even_unwitnessed():
    """A target that probes CLEAN (not a probe error) is never a blind landing — evidence exists."""
    res, fb = _run(_world({"errs": 0, "total": 8, "slow": 0}), target_evidence=True, causal=True)
    assert res["status"] == "mitigated"
    assert incidents.get("blind-test").get("blind_landing") is None
    assert fb.probe_calls == 1                                        # assessed on the first probe
