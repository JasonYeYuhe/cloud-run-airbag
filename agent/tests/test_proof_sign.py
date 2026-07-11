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


# --- v6 DSSE emit: a cosign-verifiable in-toto attestation BESIDE the legacy envelope ---------------
_MOCK_SIG = {"algorithm": "EC_SIGN_P256_SHA256", "key": "k", "signature": "sig"}


def test_dsse_off_persists_only_the_legacy_envelope(monkeypatch):
    mock.reset()
    monkeypatch.setattr(config, "GEMINI_API_KEY", "")
    monkeypatch.setattr(config, "PROOF_SIGN", True)       # PROOF_DSSE default OFF
    monkeypatch.setattr(proof, "sign_digest", lambda digest, **k: dict(_MOCK_SIG))
    assert run_self_heal("inc-nodsse", "airbag-target")["status"] == "mitigated"
    rec = incidents.get("inc-nodsse")
    assert "proof" in rec and "proof_dsse" not in rec          # DSSE off -> no sibling envelope


def test_dsse_on_emits_beside_the_untouched_legacy_envelope(monkeypatch):
    mock.reset()
    monkeypatch.setattr(config, "GEMINI_API_KEY", "")
    monkeypatch.setattr(config, "PROOF_SIGN", True)
    monkeypatch.setattr(config, "PROOF_DSSE", True)
    monkeypatch.setattr(proof, "sign_digest", lambda digest, **k: dict(_MOCK_SIG))
    assert run_self_heal("inc-dsse", "airbag-target")["status"] == "mitigated"
    rec = incidents.get("inc-dsse")
    # the legacy envelope is UNTOUCHED (beside, never inside): still exactly what build_signed produces
    assert rec["proof"]["signature"]["signature"] == "sig"
    assert "dsse" not in rec["proof"] and "payloadType" not in rec["proof"]
    # a sibling DSSE envelope, cosign-shaped, whose subject binds the SAME bundle digest
    env = rec["proof_dsse"]
    assert env["payloadType"] == "application/vnd.in-toto+json"
    stmt = json.loads(base64.b64decode(env["payload"]))
    assert stmt["predicateType"] == "airbag.dev/heal-attestation/v1"
    assert stmt["subject"][0]["digest"]["sha256"] == rec["proof"]["digest"].split(":", 1)[-1]
    assert stmt["predicate"]["bundle_version"] == "airbag.heal/v1"   # the heal rides inside as predicate
    assert env["signatures"][0]["sig"] == "sig"                       # the SECOND KMS sign (over PAE)


def test_dsse_emit_is_fail_open_on_the_second_sign(monkeypatch):
    """A KMS hiccup on the SECOND (DSSE/PAE) sign must degrade to no proof_dsse while the legacy
    envelope + the heal stay intact — the whole point of bounding both signs (R1 #6)."""
    mock.reset()
    monkeypatch.setattr(config, "GEMINI_API_KEY", "")
    monkeypatch.setattr(config, "PROOF_SIGN", True)
    monkeypatch.setattr(config, "PROOF_DSSE", True)
    calls = {"n": 0}

    def _flaky(digest, **k):
        calls["n"] += 1
        return dict(_MOCK_SIG) if calls["n"] == 1 else None   # 1st = bundle sign OK; 2nd = PAE sign FAILS

    monkeypatch.setattr(proof, "sign_digest", _flaky)
    assert run_self_heal("inc-dsse-failopen", "airbag-target")["status"] == "mitigated"
    rec = incidents.get("inc-dsse-failopen")
    assert rec["proof"]["signature"]["signature"] == "sig"    # legacy envelope signed fine
    assert rec.get("proof_dsse") is None                      # DSSE degraded -> None (reset), heal unaffected


def test_dsse_not_emitted_when_the_legacy_envelope_is_unsigned(monkeypatch):
    """If signing fails entirely (unsigned legacy), we must NOT emit a signed DSSE beside it — the two
    artifacts must never disagree on provenance. proof_dsse is reset to None, not a lone signed DSSE."""
    mock.reset()
    monkeypatch.setattr(config, "GEMINI_API_KEY", "")
    monkeypatch.setattr(config, "PROOF_SIGN", True)
    monkeypatch.setattr(config, "PROOF_DSSE", True)
    monkeypatch.setattr(proof, "sign_digest", lambda digest, **k: None)   # signing fails -> unsigned
    assert run_self_heal("inc-dsse-unsigned", "airbag-target")["status"] == "mitigated"
    rec = incidents.get("inc-dsse-unsigned")
    assert "signature" not in rec["proof"]                    # legacy is unsigned (fail-open)
    assert rec.get("proof_dsse") is None                      # ... so NO signed DSSE beside it


def test_dsse_refire_overwrites_a_stale_sibling(monkeypatch):
    """_persist_proof re-fires (MITIGATED then CLOSED) and the CLOSED bundle differs. If the CLOSED
    stamp's DSSE sign hiccups, the stale MITIGATED proof_dsse must be OVERWRITTEN (to None) so it can
    never sit beside a fresher legacy proof attesting a DIFFERENT bundle."""
    from autosre.state_machine import _persist_proof
    monkeypatch.setattr(config, "PROOF_SIGN", True)
    monkeypatch.setattr(config, "PROOF_DSSE", True)
    incidents.record("inc-refire", {"incident_id": "inc-refire", "service": "svc", "status": "mitigated",
                                    "decision": {"action": "ROLLBACK"}, "events": [{"stage": "MITIGATED", "ts": 1.0}]})
    monkeypatch.setattr(proof, "sign_digest", lambda digest, **k: dict(_MOCK_SIG))   # both signs OK
    _persist_proof("inc-refire")
    assert incidents.get("inc-refire")["proof_dsse"] is not None      # first stamp emits a real DSSE
    # the record evolves to CLOSED; this stamp's SECOND (DSSE) sign fails
    incidents.record("inc-refire", {"status": "closed",
                                    "events": [{"stage": "MITIGATED", "ts": 1.0}, {"stage": "CLOSED", "ts": 2.0}]})
    calls = {"n": 0}

    def _flaky(digest, **k):
        calls["n"] += 1
        return dict(_MOCK_SIG) if calls["n"] == 1 else None

    monkeypatch.setattr(proof, "sign_digest", _flaky)
    _persist_proof("inc-refire")
    rec = incidents.get("inc-refire")
    assert rec["proof"]["bundle"]["status"] == "closed"              # legacy refreshed to the CLOSED bundle
    assert rec.get("proof_dsse") is None                             # stale MITIGATED DSSE overwritten, not left


# --- v6 Phase 2: a SIGNED heal appends to the hash-chained transparency log (flag-gated) ------------
def test_signed_heal_appends_to_the_transparency_log(monkeypatch):
    from autosre import transparency
    mock.reset()
    monkeypatch.setattr(config, "GEMINI_API_KEY", "")
    monkeypatch.setattr(config, "PROOF_SIGN", True)
    monkeypatch.setattr(config, "TRANSPARENCY_LOG", True)
    monkeypatch.setattr(proof, "sign_digest", lambda digest, **k: dict(_MOCK_SIG))
    assert run_self_heal("inc-tlog", "airbag-target")["status"] == "mitigated"
    entries = transparency.entries()
    mine = [e for e in entries if e["incident_id"] == "inc-tlog"]
    assert len(mine) == 1 and mine[0]["terminal_status"] == "mitigated"
    assert mine[0]["bundle_digest"] == incidents.get("inc-tlog")["proof"]["digest"]   # binds the signed proof


def test_transparency_log_flag_off_writes_nothing(monkeypatch):
    from autosre import transparency
    mock.reset()
    monkeypatch.setattr(config, "GEMINI_API_KEY", "")
    monkeypatch.setattr(config, "PROOF_SIGN", True)              # TRANSPARENCY_LOG default OFF
    monkeypatch.setattr(proof, "sign_digest", lambda digest, **k: dict(_MOCK_SIG))
    assert run_self_heal("inc-notlog", "airbag-target")["status"] == "mitigated"
    assert transparency.head() is None                          # byte-identical: no chain


def test_unsigned_heal_is_not_logged(monkeypatch):
    """AND-ed with PROOF_SIGN: an unsigned heal has no signature to log, so it never enters the chain
    (the auditor's coverage check names it as unlogged instead)."""
    from autosre import transparency
    mock.reset()
    monkeypatch.setattr(config, "GEMINI_API_KEY", "")
    monkeypatch.setattr(config, "PROOF_SIGN", True)
    monkeypatch.setattr(config, "TRANSPARENCY_LOG", True)
    monkeypatch.setattr(proof, "sign_digest", lambda digest, **k: None)   # signing fails -> unsigned
    assert run_self_heal("inc-unsigned-log", "airbag-target")["status"] == "mitigated"
    assert transparency.head() is None
