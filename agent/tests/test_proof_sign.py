"""v5 Phase 4.2 — KMS-signed proof bundle. Signing side (proof.sign_digest / build_signed, fail-open),
the persist wiring at MITIGATED, and an offline round-trip through scripts/verify-proof.py (integrity,
tamper, wrong-key) using a locally-generated EC-P256 key to mimic Cloud KMS. Honesty: proves
PROVENANCE, not decision correctness."""
import base64
import hashlib
import importlib.util
import json
import threading
import time as _time
from pathlib import Path

from autosre import config, incidents, proof
from autosre.backends import mock
from autosre.state_machine import run_self_heal

_REC = {"incident_id": "inc-x", "service": "svc", "status": "mitigated",
        "decision": {"action": "ROLLBACK", "confidence": 0.9}, "error_before": 0.12,
        "error_after": 0.0, "rolled_back_to": "svc-good", "events": [{"stage": "MITIGATED", "ts": 1.0}]}

# load the offline verifier (a standalone script) as a module so we test the REAL verify() logic
_VP_PATH = Path(__file__).resolve().parents[2] / "scripts" / "verify-proof.py"
_spec = importlib.util.spec_from_file_location("verify_proof", _VP_PATH)
verify_proof = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(verify_proof)


def _locally_sign(bundle: dict):
    """Mimic Cloud KMS EC_SIGN_P256_SHA256: sign sha256(canonical) with a fresh EC-P256 key. Returns
    (proof_dict, public_pem)."""
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    priv = ec.generate_private_key(ec.SECP256R1())
    pub_pem = priv.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    canonical = json.dumps(bundle, sort_keys=True, separators=(",", ":"), default=str)
    digest = "sha256:" + hashlib.sha256(canonical.encode()).hexdigest()
    der = priv.sign(canonical.encode(), ec.ECDSA(hashes.SHA256()))
    return ({"bundle": bundle, "digest": digest,
             "signature": {"algorithm": "EC_SIGN_P256_SHA256", "key": "test-key",
                           "signature": base64.b64encode(der).decode()}}, pub_pem)


# --- signing side: fail-open + envelope -----------------------------------------------------------
def test_build_signed_flag_off_is_unsigned():
    out = proof.build_signed(_REC)                       # PROOF_SIGN default off
    assert "signature" not in out and out["digest"].startswith("sha256:")


def test_sign_degrades_without_kms_key(monkeypatch):
    monkeypatch.setattr(config, "PROOF_SIGN", True)
    monkeypatch.setattr(config, "KMS_KEY", "")           # on, but no key -> can't sign -> degrade
    assert proof.sign_digest("sha256:" + "ab" * 32) is None
    assert "signature" not in proof.build_signed(_REC)


def test_sign_digest_builds_envelope_with_mocked_kms(monkeypatch):
    monkeypatch.setattr(config, "PROOF_SIGN", True)
    monkeypatch.setattr(config, "KMS_KEY", "projects/p/locations/l/keyRings/r/cryptoKeys/k/cryptoKeyVersions/1")

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

    monkeypatch.setattr(proof.httpx, "post", lambda *a, **k: _Resp())
    env = proof.sign_digest("sha256:" + "ab" * 32)
    assert env["algorithm"] == "EC_SIGN_P256_SHA256" and env["signature"] == "YmFzZTY0c2ln"
    assert env["key"].endswith("cryptoKeyVersions/1") and "PROVENANCE only" in env["note"]


def test_build_signed_degrades_on_kms_error(monkeypatch):
    monkeypatch.setattr(config, "PROOF_SIGN", True)
    monkeypatch.setattr(config, "KMS_KEY", "projects/p/.../cryptoKeyVersions/1")

    def _boom(scopes=None):
        raise RuntimeError("no ADC in this environment")

    monkeypatch.setattr("google.auth.default", _boom)
    out = proof.build_signed(_REC)
    assert "signature" not in out and out["digest"].startswith("sha256:")   # fail-open: digest intact


# --- R1 #6: BOTH KMS network calls are wall-clock bounded (a hang can't extend the terminal stamp) --
# The terminal MITIGATED/CLOSED stamp calls sign_digest; an UNBOUNDED creds.refresh (previously) or a
# KMS POST with a per-op timeout but no TOTAL deadline could stall a completed heal's settlement — and
# the DSSE borrow DOUBLES that terminal-stamp KMS exposure, so bounding is mandatory before DSSE lands.
def test_sign_digest_bounds_a_hung_token_refresh(monkeypatch):
    monkeypatch.setattr(config, "PROOF_SIGN", True)
    monkeypatch.setattr(config, "KMS_KEY", "projects/p/l/keyRings/r/cryptoKeys/k/cryptoKeyVersions/1")
    released = threading.Event()

    class _Creds:
        token = "tok"

        def refresh(self, req):
            released.wait(10)                 # a token endpoint that hangs (released in teardown)

    monkeypatch.setattr("google.auth.default", lambda scopes=None: (_Creds(), "p"))
    # the POST must never be reached — the refresh bound short-circuits first. Record entry as an
    # OBSERVABLE list append (a raised guard would be swallowed by sign_digest's fail-open `except`).
    posts = []
    monkeypatch.setattr(proof.httpx, "post", lambda *a, **k: posts.append(1))
    try:
        t0 = _time.monotonic()
        assert proof.sign_digest("sha256:" + "ab" * 32, refresh_timeout_s=0.1, kms_timeout_s=0.1) is None
        assert _time.monotonic() - t0 < 5     # returned on the 0.1s bound, not the 10s hang
        assert posts == []                    # the refresh bound short-circuited BEFORE the KMS POST
    finally:
        released.set()                        # let the abandoned worker thread exit immediately


def test_sign_digest_bounds_a_hung_kms_post(monkeypatch):
    monkeypatch.setattr(config, "PROOF_SIGN", True)
    monkeypatch.setattr(config, "KMS_KEY", "projects/p/l/keyRings/r/cryptoKeys/k/cryptoKeyVersions/1")
    released = threading.Event()

    class _Creds:
        token = "tok"

        def refresh(self, req):
            pass

    monkeypatch.setattr("google.auth.default", lambda scopes=None: (_Creds(), "p"))

    posts = []

    def _hung_post(*a, **k):
        posts.append(1)                       # the POST WAS entered (refresh succeeded) ...
        released.wait(10)                     # ... then the KMS endpoint hangs; the bound must cut it off

    monkeypatch.setattr(proof.httpx, "post", _hung_post)
    try:
        t0 = _time.monotonic()
        assert proof.sign_digest("sha256:" + "ab" * 32, refresh_timeout_s=1.0, kms_timeout_s=0.1) is None
        assert _time.monotonic() - t0 < 5     # returned on the 0.1s KMS bound, not the 10s hang
        assert posts == [1]                   # the KMS POST was reached, THEN wall-clock bounded
    finally:
        released.set()


# --- offline verify round-trip (scripts/verify-proof.py) ------------------------------------------
def test_verify_signed_roundtrip_ok():
    signed, pem = _locally_sign(proof.build(_REC)["bundle"])
    assert signed["bundle"]["bundle_version"] == "airbag.heal/v1"   # v6: a bundle_version bundle verifies E2E
    r = verify_proof.verify(signed, pem)
    assert r["integrity_ok"] is True and r["signature_ok"] is True and r["signed"] is True


def test_verify_tampered_bundle_fails_integrity():
    signed, pem = _locally_sign(proof.build(_REC)["bundle"])
    signed["bundle"]["service"] = "attacker-swapped"      # tamper AFTER signing
    r = verify_proof.verify(signed, pem)
    assert r["integrity_ok"] is False


def test_verify_wrong_key_fails_signature():
    signed, _ = _locally_sign(proof.build(_REC)["bundle"])
    _, other_pem = _locally_sign(proof.build(_REC)["bundle"])   # a DIFFERENT key's public PEM
    r = verify_proof.verify(signed, other_pem)
    assert r["integrity_ok"] is True and r["signature_ok"] is False


def test_verify_unsigned_bundle_is_integrity_only():
    r = verify_proof.verify(proof.build(_REC), None)
    assert r["integrity_ok"] is True and r["signature_ok"] is None and r["signed"] is False


# --- persist wiring: a signed heal stamps the snapshot on the record ------------------------------
def test_heal_persists_signed_proof_snapshot(monkeypatch):
    mock.reset()
    monkeypatch.setattr(config, "GEMINI_API_KEY", "")
    monkeypatch.setattr(config, "PROOF_SIGN", True)
    monkeypatch.setattr(proof, "sign_digest",
                        lambda digest: {"algorithm": "EC_SIGN_P256_SHA256", "key": "k", "signature": "sig"})
    res = run_self_heal("inc-proof", "airbag-target")
    assert res["status"] == "mitigated"
    rec = incidents.get("inc-proof")
    assert rec["proof"]["signature"]["signature"] == "sig"
    assert rec["proof"]["digest"].startswith("sha256:")


def test_heal_flag_off_persists_no_proof(monkeypatch):
    mock.reset()
    monkeypatch.setattr(config, "GEMINI_API_KEY", "")     # PROOF_SIGN default off
    assert run_self_heal("inc-noproof", "airbag-target")["status"] == "mitigated"
    assert incidents.get("inc-noproof").get("proof") is None   # byte-identical: no snapshot
