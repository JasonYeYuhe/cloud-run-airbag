"""Phase 1.2 TDD — the auditor's counter-signed attestation + fetch-context binding.

The counter-signer signs the DIGEST (Prehashed) exactly as Cloud KMS asymmetricSign does; the kernel
(verify.attest) verifies over the canonical bytes with ECDSA(SHA256) — the two are consistent, so a
locally-signed attestation round-trips through the real kernel. All local keys (no GCP): the agent's
heal-proof key, and the auditor's OWN independent counter-sign key.
"""
import base64
import hashlib
import json

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, utils

import attestation
import verify

_AGENT_KEY = ("projects/airbag-hack-260628/locations/asia-northeast1/keyRings/airbag/"
              "cryptoKeys/airbag-proof/cryptoKeyVersions/1")
_AUDITOR_KEY = ("projects/airbag-hack-260628/locations/asia-northeast1/keyRings/airbag/"
                "cryptoKeys/airbag-auditor/cryptoKeyVersions/1")


def _bundle(incident_id: str = "inc-A", ts: float = 1_783_094_600.0) -> dict:
    return {
        "incident_id": incident_id, "service": "airbag-target", "status": "mitigated",
        "decision": {"action": "ROLLBACK", "confidence": 0.7,
                     "reasoning": "statistical gate FAIL — latency over SLO", "_source": "heuristic"},
        "recovery": {"error_before": 0.12, "error_after": 0.0, "rolled_back_to": "airbag-target-00024",
                     "restored_to": None, "recovery_seconds": 117.2},
        "transitions": [{"stage": "RUN_START", "ts": ts - 100.0}, {"stage": "MITIGATED", "ts": ts}],
    }


def _canonical(bundle: dict) -> str:
    return json.dumps(bundle, sort_keys=True, separators=(",", ":"), default=str)


def _sign_proof(bundle: dict, key_name: str = _AGENT_KEY):
    """Agent-style heal proof: sign the canonical BUNDLE (ECDSA/SHA256). Returns (proof, agent_pem)."""
    priv = ec.generate_private_key(ec.SECP256R1())
    pem = priv.public_key().public_bytes(serialization.Encoding.PEM,
                                         serialization.PublicFormat.SubjectPublicKeyInfo)
    canonical = _canonical(bundle)
    digest = "sha256:" + hashlib.sha256(canonical.encode()).hexdigest()
    der = priv.sign(canonical.encode(), ec.ECDSA(hashes.SHA256()))
    return ({"bundle": bundle, "digest": digest,
             "signature": {"algorithm": "EC_SIGN_P256_SHA256", "key": key_name,
                           "signature": base64.b64encode(der).decode()}}, pem)


def _unsigned_proof(bundle: dict) -> dict:
    canonical = _canonical(bundle)
    return {"bundle": bundle, "digest": "sha256:" + hashlib.sha256(canonical.encode()).hexdigest()}


def _local_signer(key_name: str = _AUDITOR_KEY):
    """The auditor's counter-sign: sign the DIGEST bytes (Prehashed) as KMS does. Returns
    (signer_callable, auditor_pem)."""
    priv = ec.generate_private_key(ec.SECP256R1())
    pem = priv.public_key().public_bytes(serialization.Encoding.PEM,
                                         serialization.PublicFormat.SubjectPublicKeyInfo)

    def _sign(digest: str):
        raw = bytes.fromhex(digest.split(":", 1)[-1])
        der = priv.sign(raw, ec.ECDSA(utils.Prehashed(hashes.SHA256())))
        return {"algorithm": "EC_SIGN_P256_SHA256", "key": key_name,
                "signature": base64.b64encode(der).decode(), "signed_at": 1.0, "note": "test"}

    return _sign, pem


def _attest(proof, raw, *, agent_pem, expected_key=_AGENT_KEY, requested="inc-A",
            signer=None, auditor_pem=None, signed_not_before=None):
    if signer is None:
        signer, auditor_pem = _local_signer()
    env = attestation.verify_and_attest(
        proof, raw, expected_pem=agent_pem, expected_key=expected_key,
        agent_url="https://agent.example.run.app", requested_incident_id=requested,
        http_status=200, verified_at=1_783_100_000.0, signer=signer, signed_not_before=signed_not_before)
    return env, auditor_pem


# --- the counter-signature round-trips through the SAME kernel --------------------------------------
def test_counter_signed_attestation_is_offline_verifiable():
    proof, agent_pem = _sign_proof(_bundle("inc-A"))
    env, auditor_pem = _attest(proof, json.dumps(proof).encode(), agent_pem=agent_pem)
    # the attested VERDICT: the heal itself is SIGNED-VERIFIED
    assert env["bundle"]["tri_state"] == verify.SIGNED_VERIFIED
    assert env["bundle"]["attestation_version"] == attestation.ATTESTATION_VERSION
    # the ATTESTATION ENVELOPE itself re-verifies through the kernel against the AUDITOR's pinned key
    v = verify.attest(env, expected_pem=auditor_pem, expected_key=_AUDITOR_KEY)
    assert v["tri_state"] == verify.SIGNED_VERIFIED and v["verified_signer"] == _AUDITOR_KEY


def test_attestation_counter_signed_by_wrong_auditor_key_fails_the_kernel():
    """The auditor pin applies to the attestation too: a counter-sig claiming an unexpected auditor key
    FAILs (the same pinned-signer property, now on the auditor's own identity)."""
    proof, agent_pem = _sign_proof(_bundle("inc-A"))
    signer, auditor_pem = _local_signer(key_name=_AUDITOR_KEY.replace("cryptoKeyVersions/1",
                                                                      "cryptoKeyVersions/9"))
    env, _ = _attest(proof, b"{}", agent_pem=agent_pem, signer=signer, auditor_pem=auditor_pem)
    v = verify.attest(env, expected_pem=auditor_pem, expected_key=_AUDITOR_KEY)  # pin /1, envelope /9
    assert v["signature_ok"] is True and v["signer_pinned"] is False and v["tri_state"] == verify.FAIL


# --- fetch-context binding (Round-3 #1) ------------------------------------------------------------
def test_incident_id_mismatch_is_fail_even_for_a_valid_bundle():
    proof, agent_pem = _sign_proof(_bundle("inc-A"))
    env, auditor_pem = _attest(proof, json.dumps(proof).encode(), agent_pem=agent_pem, requested="inc-B")
    assert env["bundle"]["tri_state"] == verify.FAIL              # asked for B, got a valid A -> FAIL
    assert env["bundle"]["fetch"]["incident_id_match"] is False
    assert env["bundle"]["fetch"]["requested_incident_id"] == "inc-B"
    # the attestation itself is still a validly counter-signed statement (that says FAIL)
    v = verify.attest(env, expected_pem=auditor_pem, expected_key=_AUDITOR_KEY)
    assert v["tri_state"] == verify.SIGNED_VERIFIED


def test_fetch_context_binds_the_exact_raw_bytes():
    proof, agent_pem = _sign_proof(_bundle("inc-A"))
    raw = b'{"exactly":"these fetched bytes"}'
    env, _ = _attest(proof, raw, agent_pem=agent_pem)
    fetch = env["bundle"]["fetch"]
    assert fetch["raw_fetched_digest"] == "sha256:" + hashlib.sha256(raw).hexdigest()
    assert fetch["agent_url"] == "https://agent.example.run.app" and fetch["http_status"] == 200


# --- domain separation + fail-open ------------------------------------------------------------------
def test_domain_separation_tag_distinguishes_attestation_from_heal_bundle():
    proof, agent_pem = _sign_proof(_bundle("inc-A"))
    env, _ = _attest(proof, b"{}", agent_pem=agent_pem)
    assert attestation.is_attestation(env["bundle"]) is True
    assert attestation.is_attestation(proof["bundle"]) is False   # a heal bundle carries no tag


def test_fail_open_when_counter_signer_returns_none():
    proof, agent_pem = _sign_proof(_bundle("inc-A"))
    env, _ = _attest(proof, b"{}", agent_pem=agent_pem, signer=lambda digest: None, auditor_pem=None)
    assert "signature" not in env                                  # unsigned "unattested", no crash
    assert env["bundle"]["attestation_version"] == attestation.ATTESTATION_VERSION
    v = verify.attest(env, expected_pem=None, expected_key=_AUDITOR_KEY)
    assert v["tri_state"] == verify.INTEGRITY_ONLY                 # honest: unsigned attestation


def test_fail_open_when_counter_signer_raises():
    """FAIL-OPEN must hold even if the signer RAISES (not just returns None) — the out-of-band audit
    can never be stopped by a counter-sign failure."""
    proof, agent_pem = _sign_proof(_bundle("inc-A"))

    def _boom(digest):
        raise RuntimeError("KMS exploded")

    env, _ = _attest(proof, b"{}", agent_pem=agent_pem, signer=_boom, auditor_pem=None)
    assert "signature" not in env
    assert env["bundle"]["attestation_version"] == attestation.ATTESTATION_VERSION


def test_attestation_of_an_unsigned_heal_records_integrity_only_but_is_itself_signed():
    unsigned = _unsigned_proof(_bundle("inc-A"))
    env, auditor_pem = _attest(unsigned, json.dumps(unsigned).encode(), agent_pem=None)
    assert env["bundle"]["tri_state"] == verify.INTEGRITY_ONLY     # the HEAL is unsigned
    v = verify.attest(env, expected_pem=auditor_pem, expected_key=_AUDITOR_KEY)
    assert v["tri_state"] == verify.SIGNED_VERIFIED                # the AUDITOR still counter-signs


def test_hostile_published_proof_never_crashes_the_audit():
    """verify_and_attest must fail open (return an envelope) on a hostile/garbled published proof, never
    raise — else a compromised agent could suppress its own auditor with a one-line malformed payload."""
    signer, _ = _local_signer()
    for proof, raw in [({"bundle": "x", "digest": "sha256:" + "0" * 64}, b"x"),
                       ({"bundle": [1, 2], "digest": "sha256:" + "0" * 64}, b"[]"),
                       ({"bundle": {"transitions": None}, "digest": "sha256:" + "0" * 64}, b"{}"),
                       (["not an object"], b"[]")]:
        env = attestation.verify_and_attest(
            proof, raw, expected_pem=None, expected_key=_AGENT_KEY, agent_url="u",
            requested_incident_id="inc-A", http_status=200, verified_at=1.0, signer=signer)  # no raise
        assert env["bundle"]["tri_state"] == verify.FAIL              # garbage -> honest FAIL
        assert env["bundle"]["attestation_version"] == attestation.ATTESTATION_VERSION


# --- the prod KMS counter-signer (mocked; real GCP deferred to deploy) ------------------------------
def test_sign_digest_kms_builds_envelope_with_mocked_kms(monkeypatch):
    class _Creds:
        token = "tok"

        def refresh(self, req):
            pass

    monkeypatch.setattr("google.auth.default", lambda scopes=None: (_Creds(), "p"))

    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"signature": "YmFzZTY0c2ln"}

    monkeypatch.setattr("httpx.post", lambda *a, **k: _Resp())
    env = attestation.sign_digest_kms("sha256:" + "ab" * 32, _AUDITOR_KEY)
    assert env["algorithm"] == "EC_SIGN_P256_SHA256" and env["signature"] == "YmFzZTY0c2ln"
    assert env["key"] == _AUDITOR_KEY


def test_sign_digest_kms_fails_open_on_error(monkeypatch):
    def _boom(scopes=None):
        raise RuntimeError("no ADC in this environment")

    monkeypatch.setattr("google.auth.default", _boom)
    assert attestation.sign_digest_kms("sha256:" + "ab" * 32, _AUDITOR_KEY) is None
