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
    assert b["issuer"] == "spiffe://airbag.dev/agent"          # v6: SPIFFE-style workload identity
    assert b["decision"]["action"] == "ROLLBACK"
    assert b["detection"]["verdict"] == "FAIL" and b["detection"]["signals"] == {"latency": {"verdict": "FAIL"}}
    assert b["causal"]["verdict"] == "CAUSAL"
    assert b["recovery"]["recovery_seconds"] == 30.0            # 130 - 100
    assert b["fix_pr"].endswith("/pull/9")
    assert [t["stage"] for t in b["transitions"]] == ["RECEIVED", "ANALYZED", "CAUSAL", "MITIGATED"]
    assert b["trigger_evidence_digest"].startswith("sha256:")   # v6: intent binding rides the bundle
    assert b["externalParameters"]["action"] == "ROLLBACK"      # v6: LLM-suggested (advisory)
    assert "rollback_revision" in b["internalParameters"]       # v6: FSM-resolved (deterministic)
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


def test_slsa_parameter_split_quarantines_the_llm():
    """v6 §1b.3 #6: externalParameters = what the advisory tier SUGGESTED; internalParameters = what the
    deterministic FSM RESOLVED — including the killer case where the LLM said OBSERVE and the FSM
    PROMOTED a rollback + picked the target (the quarantine made auditable, not just asserted)."""
    rec = _rec()
    rec["autonomy"] = "L2"
    rec["decision"].update({
        "action": "ROLLBACK", "_source": "gemini-adk",                     # the FSM-RESOLVED action
        "_suggested": {"action": "OBSERVE", "confidence": 0.3, "reasoning": "looks fine to me"},
        "rollback_revision": "airbag-target-00013", "bad_revision": "airbag-target-00022",
        "_target_source": "ledger", "_target_overridden": True, "_promoted": True})
    b = proof.build(rec)["bundle"]
    # externalParameters = what the LLM ACTUALLY proposed (an OBSERVE), not the resolved action
    assert b["externalParameters"] == {"action": "OBSERVE", "confidence": 0.3,
                                       "reasoning": "looks fine to me", "source": "gemini-adk"}
    # internalParameters = the deterministic FSM's resolution — it OVERRODE the LLM to a rollback
    assert b["internalParameters"] == {"action": "ROLLBACK", "rollback_revision": "airbag-target-00013",
                                       "bad_revision": "airbag-target-00022", "target_source": "ledger",
                                       "target_overridden": True, "promoted": True, "autonomy_level": "L2"}
    assert b["externalParameters"]["action"] != b["internalParameters"]["action"]   # the quarantine, visible


def test_slsa_split_falls_back_for_pre_v6_records():
    """A record written before the _suggested snapshot existed: externalParameters degrades gracefully
    to the recorded (resolved) decision rather than dropping the split."""
    rec = _rec()                                       # no _suggested key
    b = proof.build(rec)["bundle"]
    assert b["externalParameters"]["action"] == "ROLLBACK"
    assert b["externalParameters"]["source"] == "gemini-adk"
    assert b["internalParameters"]["action"] == "ROLLBACK"


def test_trigger_evidence_digest_binds_the_triggering_signals():
    """v6 §1b.3 #7: a recomputable sha256 over the triggering signal evidence (anti-replay)."""
    rec = _rec()
    b = proof.build(rec)["bundle"]
    assert b["trigger_evidence_digest"].startswith("sha256:")
    expect = "sha256:" + hashlib.sha256(json.dumps(
        {"detection": b["detection"], "evidence": rec["decision"].get("evidence")},
        sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()
    assert b["trigger_evidence_digest"] == expect                    # recomputable by an auditor
    rec2 = _rec()
    rec2["events"][1]["verdict"] = "PASS"                            # a DIFFERENT triggering verdict
    assert proof.build(rec2)["bundle"]["trigger_evidence_digest"] != b["trigger_evidence_digest"]


def test_v6_evidence_fields_are_presence_keyed():
    """A sparse bundle (no ANALYZED detection, no decision) omits the trigger digest AND the SLSA split;
    only bundle_version is UNCONDITIONAL."""
    b = proof.build({"incident_id": "i", "events": []})["bundle"]
    assert "trigger_evidence_digest" not in b
    assert "externalParameters" not in b and "internalParameters" not in b
    assert b["bundle_version"] == "airbag.heal/v1"              # unconditional
    assert b["issuer"] == "spiffe://airbag.dev/agent"          # unconditional


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


def test_served_trust_anchor_routes():
    """v6 Phase 3: /.well-known serves the COMMITTED pubkey (byte-identical) + the role-tagged registry."""
    from pathlib import Path
    from fastapi.testclient import TestClient
    import app as appmod
    c = TestClient(appmod.app)
    r = c.get("/.well-known/airbag-proof-pubkey.pem")
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/x-pem-file")
    committed = (Path(appmod.__file__).resolve().parent.parent / "scripts" / "airbag-proof-pubkey.pem").read_bytes()
    assert r.content == committed                              # the served bytes ARE the committed root
    reg = c.get("/.well-known/airbag-registry.json").json()
    assert reg["version"] == 1 and reg["threshold"] == 1
    assert {k["role"] for k in reg["keys"]} == {"heal-proof-signer", "attestation-signer"}   # Round 2 #6
    heal = next(k for k in reg["keys"] if k["role"] == "heal-proof-signer")
    assert heal["resource"].endswith("airbag-proof/cryptoKeyVersions/1")
    assert heal["algorithm"] == "EC_SIGN_P256_SHA256"


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
