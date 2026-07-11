"""The tamper-evident incident proof bundle (proof.py) — Phase 3.3. A canonical stitch of the
incident evidence + a sha256 content digest that proves INTEGRITY (recompute → compare), NOT a
cryptographic signature."""
import hashlib
import json

from autosre import proof


def _rec():
    return {
        "incident_id": "inc-1", "service": "airbag-target", "status": "mitigated",
        "decision": {"action": "ROLLBACK", "confidence": 0.9, "reasoning": "latency regression",
                     "_source": "gemini-adk"},
        "error_before": 1.0, "error_after": 0.0, "rolled_back_to": "airbag-target-00013",
        "pr_url": "https://github.com/x/y/pull/9",
        "events": [
            {"stage": "RECEIVED", "ts": 100.0, "msg": "incident"},
            {"stage": "ANALYZED", "ts": 101.0, "verdict": "FAIL", "reason": "latency 4/4",
             "signals": {"latency": {"verdict": "FAIL"}}},
            {"stage": "CAUSAL", "ts": 102.0, "verdict": "CAUSAL", "msg": "target healthy"},
            {"stage": "MITIGATED", "ts": 130.0, "msg": "recovered"},
        ],
    }


def test_bundle_stitches_the_evidence():
    p = proof.build(_rec())
    b = p["bundle"]
    assert b["bundle_version"] == "airbag.heal/v1"              # v6: permanent self-describing type tag
    assert b["decision"]["action"] == "ROLLBACK"
    assert b["detection"]["verdict"] == "FAIL" and b["detection"]["signals"] == {"latency": {"verdict": "FAIL"}}
    assert b["causal"]["verdict"] == "CAUSAL"
    assert b["recovery"]["recovery_seconds"] == 30.0            # 130 - 100
    assert b["fix_pr"].endswith("/pull/9")
    assert [t["stage"] for t in b["transitions"]] == ["RECEIVED", "ANALYZED", "CAUSAL", "MITIGATED"]
    assert p["digest"].startswith("sha256:")


def test_digest_is_stable_and_verifiable():
    p = proof.build(_rec())
    # recompute the digest over the canonical bundle exactly as an auditor would
    canonical = json.dumps(p["bundle"], sort_keys=True, separators=(",", ":"), default=str)
    assert p["digest"] == "sha256:" + hashlib.sha256(canonical.encode()).hexdigest()
    assert proof.build(_rec())["digest"] == p["digest"]         # deterministic


def test_digest_changes_when_content_is_tampered():
    p1 = proof.build(_rec())
    rec = _rec()
    rec["decision"]["action"] = "OBSERVE"                       # tamper with the recorded decision
    assert proof.build(rec)["digest"] != p1["digest"]


def test_report_footer_shows_the_proof_digest():
    from autosre import report
    h = report.render(_rec())
    assert "proof bundle" in h and "sha256:" in h and "/incidents/inc-1/proof" in h


def test_bundle_handles_sparse_record():
    p = proof.build({"incident_id": "i", "events": []})
    assert p["digest"].startswith("sha256:") and p["bundle"]["transitions"] == []


def test_bundle_version_is_permanent_and_rides_the_digest():
    """v6: bundle_version is UNCONDITIONAL (present on the fullest AND the sparsest bundle) and is part
    of the canonical bytes the digest covers — so it can never be stripped without breaking integrity,
    and a registry-driven verify surface can always read the artifact type in-band."""
    for rec in (_rec(), {"incident_id": "i", "events": []}):
        p = proof.build(rec)
        assert p["bundle"]["bundle_version"] == "airbag.heal/v1"
        # the tag is INSIDE the canonical json the digest is computed over (not a sidecar field)
        canonical = json.dumps(p["bundle"], sort_keys=True, separators=(",", ":"), default=str)
        assert '"bundle_version":"airbag.heal/v1"' in canonical
        assert p["digest"] == "sha256:" + hashlib.sha256(canonical.encode()).hexdigest()


def test_proof_endpoint(monkeypatch):
    from fastapi.testclient import TestClient
    import app as appmod
    from autosre import incidents
    incidents.record("inc-proof", _rec())          # persist to the (per-test reset) memory store
    c = TestClient(appmod.app)
    assert c.get("/incidents/does-not-exist/proof").status_code == 404
    r = c.get("/incidents/inc-proof/proof")
    assert r.status_code == 200 and r.json()["digest"].startswith("sha256:")
    assert r.json()["bundle"]["decision"]["action"] == "ROLLBACK"
