"""v5 Phase 5.3 — revision-delta evidence.

An LLM-free, DETERMINISTIC spec diff (image digest, env var NAMES — never values, resource limits) of
the bad (currently-serving) revision vs the rollback TARGET, attached to the incident
record/report/proof. Behind AIRBAG_REVISION_DELTA (default OFF -> byte-identical to v4). The honest
"what changed" FORWARD story: a latency regression's remedy IS the rollback (no HTTP-500 code bug for
a fix-PR to repair), but an operator still deserves to see how the bad deploy differed.

Covers: the pure diff; the backend spec extractors (gcp pure extractor + the mock/local synthetic
specs + the tools façade); the wiring through the REAL run_self_heal seam (5xx AND latency incidents);
and that the flag OFF leaves the record, report, and signed proof digest byte-identical.
"""
import json
from types import SimpleNamespace

from autosre import config, incidents, proof, report, revision_delta, state_store, tools
from autosre.backends import gcp, local, mock
from autosre.state_machine import run_self_heal
from bench.harness import _PINNED, SERVICE, FixtureBackend

_BAD = f"{SERVICE}-00002-bad"
_GOOD = f"{SERVICE}-00001-good"


# --- the pure diff -------------------------------------------------------------------------------
def test_diff_detects_image_change():
    bad = {"image": "app@sha256:BAD", "env_names": ["PORT"], "limits": {"cpu": "1"}}
    tgt = {"image": "app@sha256:GOOD", "env_names": ["PORT"], "limits": {"cpu": "1"}}
    d = revision_delta.diff(bad, tgt)
    assert d["image_changed"] is True
    assert d["image_bad"].endswith("BAD") and d["image_target"].endswith("GOOD")
    assert d["env_added"] == [] and d["env_removed"] == []
    assert d["limits_changed"] is False


def test_diff_detects_env_add_and_remove():
    # bad = the serving revision; env_added = names it INTRODUCED (in bad, not target)
    bad = {"image": "x", "env_names": ["PORT", "FAULT_MODE", "NEW"], "limits": {}}
    tgt = {"image": "x", "env_names": ["PORT", "OLD"], "limits": {}}
    d = revision_delta.diff(bad, tgt)
    assert d["image_changed"] is False
    assert d["env_added"] == ["FAULT_MODE", "NEW"]   # sorted, present in bad only
    assert d["env_removed"] == ["OLD"]               # present in target only


def test_diff_detects_limits_change():
    bad = {"image": "x", "env_names": [], "limits": {"memory": "256Mi", "cpu": "1"}}
    tgt = {"image": "x", "env_names": [], "limits": {"memory": "512Mi", "cpu": "1"}}
    d = revision_delta.diff(bad, tgt)
    assert d["limits_changed"] is True
    assert d["limits_bad"]["memory"] == "256Mi" and d["limits_target"]["memory"] == "512Mi"


def test_diff_no_change_all_false():
    s = {"image": "x", "env_names": ["A", "B"], "limits": {"cpu": "1"}}
    d = revision_delta.diff(dict(s), dict(s))
    assert d["image_changed"] is False and d["limits_changed"] is False
    assert d["env_added"] == [] and d["env_removed"] == []


def test_diff_tolerates_empty_and_none_specs():
    for a, b in ((None, None), ({}, {}), (None, {"image": "x"})):
        d = revision_delta.diff(a, b)
        assert set(d) >= {"image_changed", "env_added", "env_removed", "limits_changed"}
        assert isinstance(d["env_added"], list) and isinstance(d["env_removed"], list)
    assert revision_delta.diff(None, {"image": "x"})["image_changed"] is True  # None-vs-set image


def test_diff_is_deterministic_and_json_serialisable():
    bad = {"image": "b", "env_names": ["Z", "A"], "limits": {"memory": "256Mi"}}
    tgt = {"image": "g", "env_names": ["A"], "limits": {"memory": "512Mi"}}
    d1, d2 = revision_delta.diff(bad, tgt), revision_delta.diff(bad, tgt)
    # same inputs -> byte-identical canonical form (so the proof digest is stable)
    assert json.dumps(d1, sort_keys=True) == json.dumps(d2, sort_keys=True)
    assert d1["env_added"] == ["Z"]   # sorted


# --- gcp pure extractor (no live API — duck-typed revision object) -------------------------------
def test_gcp_spec_from_revision_extracts_image_env_names_limits():
    rev = SimpleNamespace(containers=[SimpleNamespace(
        image="gcr.io/p/app@sha256:abc123",
        env=[SimpleNamespace(name="PORT", value="8080"),
             SimpleNamespace(name="FAULT_MODE", value="bug"),
             SimpleNamespace(name="", value="ignored")],   # nameless env skipped
        resources=SimpleNamespace(limits={"cpu": "1", "memory": "512Mi"}))])
    spec = gcp._spec_from_revision(rev)
    assert spec["image"] == "gcr.io/p/app@sha256:abc123"
    assert spec["env_names"] == ["FAULT_MODE", "PORT"]          # sorted NAMES, blank dropped
    assert spec["limits"] == {"cpu": "1", "memory": "512Mi"}
    # the load-bearing privacy invariant: env VALUES must never leak into the spec
    assert "8080" not in json.dumps(spec) and "bug" not in json.dumps(spec)


def test_gcp_spec_from_revision_tolerates_missing_fields():
    assert gcp._spec_from_revision(SimpleNamespace(containers=[])) == {
        "image": None, "env_names": [], "limits": {}}
    rev = SimpleNamespace(containers=[SimpleNamespace(image="i", env=None, resources=None)])
    assert gcp._spec_from_revision(rev) == {"image": "i", "env_names": [], "limits": {}}


# --- backend synthetic specs + the tools façade --------------------------------------------------
def test_local_and_mock_specs_differ_bad_vs_good_names_only():
    for backend in (local, mock):
        bad = backend.revision_spec(SERVICE, "asia-northeast1", _BAD)
        good = backend.revision_spec(SERVICE, "asia-northeast1", _GOOD)
        d = revision_delta.diff(bad, good)
        assert d["image_changed"] is True                 # bad & good differ by image digest
        assert "FAULT_MODE" in d["env_added"]             # the bad revision carries the fault env
        # synthetic specs never carry values either (names + image + limits only)
        assert set(bad) == {"image", "env_names", "limits"}


def test_tools_facade_dispatches_revision_spec_to_active_backend():
    # conftest pins the mock backend for the suite
    spec = tools.revision_spec(SERVICE, "asia-northeast1", _BAD)
    assert set(spec) == {"image", "env_names", "limits"}
    assert "FAULT_MODE" in spec["env_names"]


# --- wiring through the real run_self_heal seam --------------------------------------------------
_SPECS = {
    _BAD:  {"image": "app@sha256:BAD", "env_names": ["PORT", "FAULT_MODE"],
            "limits": {"cpu": "1", "memory": "256Mi"}},
    _GOOD: {"image": "app@sha256:GOOD", "env_names": ["PORT"],
            "limits": {"cpu": "1", "memory": "512Mi"}},
}


def _world(*, latency: bool):
    revs = [
        {"name": _BAD, "ready": True, "traffic_percent": 100, "create_time": "2026-07-02T00:00:00Z"},
        {"name": _GOOD, "ready": True, "traffic_percent": 0, "create_time": "2026-07-01T00:00:00Z"},
    ]
    if latency:   # a latency regression: clean 5xx, sustained over-SLO windows -> ROLLBACK, no fix-PR
        return {"revisions": revs, "error_rate": 0.0, "sample": {"errs": 0, "total": 20},
                "latency_windows": [{"slow": 18, "total": 20} for _ in range(4)],
                "rollback_clears": True, "revision_specs": _SPECS}
    return {"revisions": revs, "error_rate": 1.0, "sample": {"errs": 8, "total": 8},
            "rollback_clears": True, "revision_specs": _SPECS}


def _run(*, revision_delta_on, latency=False, backend_cls=FixtureBackend):
    saved = {k: getattr(config, k) for k in _PINNED}
    saved_gb = tools.get_backend
    fb = backend_cls(_world(latency=latency))
    try:
        for k, v in _PINNED.items():
            setattr(config, k, v)
        config.REVISION_DELTA = revision_delta_on
        if latency:
            # TWO detectors, so engine._fuse emits the per-signal `signals` breakdown that
            # _primary_signal reads (a SINGLE 'latency' detector returns its verdict verbatim -> no
            # breakdown -> _primary_signal falls through to '5xx' and the run would NOT take the
            # latency branch). 5xx PASSes here (0 errors), so the primary signal is 'latency'.
            config.SIGNALS = "5xx,latency"
        tools.get_backend = lambda: fb
        state_store.reset_memory()
        return run_self_heal("rd-test", SERVICE)
    finally:
        tools.get_backend = saved_gb
        for k, v in saved.items():
            setattr(config, k, v)


def test_5xx_record_carries_revision_delta_when_flag_on():
    res = _run(revision_delta_on=True)
    assert res["status"] == "mitigated"
    rd = incidents.get("rd-test").get("revision_delta")
    assert rd is not None
    assert rd["image_changed"] is True
    assert rd["env_added"] == ["FAULT_MODE"]     # bad rev introduced FAULT_MODE
    assert rd["env_removed"] == []
    assert rd["limits_changed"] is True          # 256Mi -> 512Mi


def test_latency_incident_record_carries_revision_delta():
    """The task's headline: a latency incident (which correctly gets NO fabricated fix-PR) still
    carries the honest 'what changed' delta on its record. This asserts the run actually took the
    primary_signal != '5xx' branch (state_machine.py) — not just that pr_url happens to be None."""
    res = _run(revision_delta_on=True, latency=True)
    assert res["status"] == "mitigated"
    # PROVE the latency branch ran: it emits the "no forward code-fix PR ... latency regression"
    # FIX_PR note (the 5xx path instead emits _open_fix_pr's "slow path not configured" message).
    fix_pr = next((e for e in res["events"] if e.get("stage") == "FIX_PR"), None)
    assert fix_pr is not None and "no forward code-fix PR" in fix_pr["msg"]
    assert "latency regression is remedied by the rollback" in fix_pr["msg"]
    rec = incidents.get("rd-test")
    assert rec.get("pr_url") is None             # latency regression -> no forward code-fix PR
    assert rec.get("revision_delta", {}).get("image_changed") is True
    assert rec["revision_delta"]["env_added"] == ["FAULT_MODE"]


def test_flag_off_no_delta_on_record():
    res = _run(revision_delta_on=False)
    assert res["status"] == "mitigated"
    assert incidents.get("rd-test").get("revision_delta") is None   # never computed/attached


class _RaisingSpecBackend(FixtureBackend):
    """A backend whose revision_spec always raises — models a get_revision/network error mid-heal."""
    def revision_spec(self, service, region, revision):
        raise RuntimeError("boom: get_revision failed")


def test_fail_open_when_revision_spec_raises():
    """FAIL-OPEN (a load-bearing invariant): a spec-fetch error during delta attachment must NEVER
    block or corrupt the heal — the rollback still mitigates and the delta is simply omitted. Without
    the try/except in _mitigate the exception would escape AFTER traffic already shifted."""
    res = _run(revision_delta_on=True, backend_cls=_RaisingSpecBackend)
    assert res["status"] == "mitigated"                             # never blocked by the spec error
    assert incidents.get("rd-test").get("revision_delta") is None   # delta omitted, record intact


# --- byte-identical guarantees in the surfaces ---------------------------------------------------
def _bare_rec():
    return {"incident_id": "i", "service": "s", "status": "mitigated", "events": [],
            "decision": {"action": "ROLLBACK", "confidence": 0.9}}


def test_proof_digest_byte_identical_without_delta_and_included_with_it():
    rec = _bare_rec()
    # revision_delta is a PRESENCE-keyed bundle field: absent -> not in the bundle -> digest is stable
    # across rebuilds; present -> it rides the signed bundle and the digest changes. (This is a
    # self-rebuild stability check; the absolute digest is not a fixed historical value — bundle_version
    # is a permanent v6 schema field, so a fresh build is no longer the v4-era bundle shape.)
    digest_no_delta = proof.build(rec)["digest"]
    assert "revision_delta" not in proof.build(rec)["bundle"]    # absent -> not in the bundle
    assert proof.build(rec)["digest"] == digest_no_delta        # ... so the digest is stable across rebuilds

    rec2 = {**rec, "revision_delta": {"image_changed": True, "env_added": ["FAULT_MODE"]}}
    signed = proof.build(rec2)
    assert signed["bundle"]["revision_delta"] == {"image_changed": True, "env_added": ["FAULT_MODE"]}
    assert signed["digest"] != digest_no_delta                 # present -> rides the signed bundle


def test_report_card_present_only_with_delta():
    assert "Revision delta" not in report.render(_bare_rec())   # absent -> card omitted (byte-identical)
    rec2 = {**_bare_rec(), "revision_delta": {
        "image_changed": True, "image_bad": "app@sha256:BAD", "image_target": "app@sha256:GOOD",
        "env_added": ["FAULT_MODE"], "env_removed": [], "limits_changed": True,
        "limits_bad": {"memory": "256Mi"}, "limits_target": {"memory": "512Mi"}}}
    h = report.render(rec2)
    assert "Revision delta" in h and "what changed" in h
    assert "FAULT_MODE" in h and "256Mi" in h and "512Mi" in h


def test_report_escapes_revision_delta_values():
    rec2 = {**_bare_rec(), "revision_delta": {
        "image_changed": True, "image_bad": "<script>evil</script>", "image_target": "ok",
        "env_added": ["<b>x</b>"], "env_removed": [], "limits_changed": False,
        "limits_bad": {}, "limits_target": {}}}
    h = report.render(rec2)
    assert "<script>evil</script>" not in h and "&lt;script&gt;" in h
