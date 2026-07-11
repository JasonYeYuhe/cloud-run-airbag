"""DSSE + in-toto heal-attestation construction (v6 Phase 1.2 borrow). The cosign-in-CI job is the
authoritative HARD GATE (Round 3 #9); these tests are the LOCAL, cosign-independent proofs that the
construction is spec-exact and cosign-EQUIVALENT: (1) PAE matches the DSSE protocol.md test vector
byte-for-byte, (2) an envelope our code builds verifies with the SAME crypto cosign uses (ECDSA-P256
over PAE), (3) --check-claims holds (sha256(blob) == the statement subject), (4) fail-open. A drift in
dsse.py that would make cosign reject the on-camera beat fails one of these BEFORE a CI round-trip."""
import base64
import hashlib
import json
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, utils

from autosre import dsse, proof

_GOLDEN = Path(__file__).resolve().parents[2] / "docs" / "proof" / "dsse-golden"


def _kms_like_signer(priv):
    """Mimic Cloud KMS EC_SIGN_P256_SHA256: sign the PROVIDED sha256 digest (Prehashed), return a
    base64 DER envelope — the exact shape proof.sign_digest returns, so build_dsse is key-agnostic."""
    def sign(digest: str):
        raw = bytes.fromhex(digest.split(":", 1)[-1])                 # sha256(PAE) bytes
        der = priv.sign(raw, ec.ECDSA(utils.Prehashed(hashes.SHA256())))
        return {"signature": base64.b64encode(der).decode(), "key": "local-ec-test"}
    return sign


def _cosign_equivalent_verify(env: dict, pub) -> bool:
    """Reproduce cosign's DSSE check with stdlib+cryptography: recompute PAE from the envelope's own
    payloadType+payload, then verify the DER signature over PAE with ECDSA-P256/SHA256 (NOT prehashed —
    we hold the full PAE message). This is byte-for-byte what `cosign verify-blob-attestation` does."""
    body = base64.b64decode(env["payload"])
    pae_bytes = dsse.pae(env["payloadType"].encode("ascii"), body)
    der = base64.b64decode(env["signatures"][0]["sig"])
    try:
        pub.verify(der, pae_bytes, ec.ECDSA(hashes.SHA256()))
        return True
    except Exception:
        return False


# --- PAE is spec-exact (the trap Round 3 #9 exists to catch) --------------------------------------
def test_pae_matches_the_dsse_protocol_test_vector():
    # secure-systems-lab/dsse protocol.md: payloadType "http://example.com/HelloWorld", body "hello world"
    got = dsse.pae(b"http://example.com/HelloWorld", b"hello world")
    assert got == b"DSSEv1 29 http://example.com/HelloWorld 11 hello world"


def test_pae_lengths_are_byte_lengths_not_char_lengths():
    # a non-ASCII body: LEN must be the UTF-8 BYTE length (em-dash is 3 bytes), not the char count
    body = "—".encode("utf-8")                                        # 3 bytes, 1 char
    assert dsse.pae(b"t", body) == b"DSSEv1 1 t 3 " + body


# --- in-toto Statement shape cosign parses --------------------------------------------------------
def test_statement_binds_subject_predicate_type_and_payload_type():
    stmt = dsse.in_toto_statement({"k": "v"}, "inc-9", "ab" * 32)
    assert stmt["_type"] == "https://in-toto.io/Statement/v1"
    assert stmt["predicateType"] == "airbag.dev/heal-attestation/v1"
    assert stmt["subject"] == [{"name": "inc-9", "digest": {"sha256": "ab" * 32}}]
    assert stmt["predicate"] == {"k": "v"}
    assert dsse.PAYLOAD_TYPE == "application/vnd.in-toto+json"


def test_missing_incident_id_falls_back_to_a_stable_subject_name():
    stmt = dsse.in_toto_statement({}, None, "cd" * 32)
    assert stmt["subject"][0]["name"] == "airbag-heal"               # never an empty/None subject name


# --- build_dsse: cosign-EQUIVALENT verification of what our code emits -----------------------------
def test_build_dsse_verifies_with_cosign_equivalent_crypto():
    priv = ec.generate_private_key(ec.SECP256R1())
    built = proof.build({"incident_id": "inc-golden", "service": "svc", "status": "mitigated",
                         "decision": {"action": "ROLLBACK", "confidence": 0.9},
                         "events": [{"stage": "MITIGATED", "ts": 1.0}]})
    subject_hex = built["digest"].split(":", 1)[-1]
    env = dsse.build_dsse(built["bundle"], "inc-golden", subject_hex, signer=_kms_like_signer(priv))
    assert env is not None and env["payloadType"] == "application/vnd.in-toto+json"
    assert _cosign_equivalent_verify(env, priv.public_key())          # the signature verifies over PAE

    # --check-claims: sha256(canonical bundle blob) == the statement subject digest
    canonical = json.dumps(built["bundle"], sort_keys=True, separators=(",", ":"), default=str)
    assert hashlib.sha256(canonical.encode()).hexdigest() == subject_hex
    stmt = json.loads(base64.b64decode(env["payload"]))
    assert stmt["subject"][0]["digest"]["sha256"] == subject_hex
    assert stmt["predicate"]["bundle_version"] == "airbag.heal/v1"     # the heal rides inside as predicate


def test_build_dsse_is_fail_open_when_the_signer_declines():
    env = dsse.build_dsse({"x": 1}, "inc", "ab" * 32, signer=lambda d: None)
    assert env is None                                                # degrade to the legacy envelope
    env2 = dsse.build_dsse({"x": 1}, "inc", "ab" * 32, signer=lambda d: {"key": "k"})  # no "signature"
    assert env2 is None


def test_a_tampered_payload_fails_cosign_equivalent_verification():
    priv = ec.generate_private_key(ec.SECP256R1())
    built = proof.build({"incident_id": "i", "events": [], "decision": {"action": "ROLLBACK"}})
    env = dsse.build_dsse(built["bundle"], "i", built["digest"].split(":", 1)[-1],
                          signer=_kms_like_signer(priv))
    # flip one byte of the predicate inside the payload -> PAE changes -> signature no longer verifies
    stmt = json.loads(base64.b64decode(env["payload"]))
    stmt["predicate"]["service"] = "attacker-swapped"
    env["payload"] = base64.b64encode(dsse.statement_bytes(stmt)).decode("ascii")
    assert _cosign_equivalent_verify(env, priv.public_key()) is False


# --- the COMMITTED golden (what the cosign-in-CI job verifies) stays internally consistent ---------
def test_committed_golden_is_self_consistent():
    """The committed docs/proof/dsse-golden/* must verify with the same crypto cosign uses AND its
    subject must equal sha256 of the committed blob (else the CI --check-claims would fail on camera)."""
    env = json.loads((_GOLDEN / "heal.intoto.dsse.json").read_text())
    blob = (_GOLDEN / "canonical-bundle.json").read_bytes()
    pub = serialization.load_pem_public_key((_GOLDEN / "cosign.pub").read_bytes())
    assert _cosign_equivalent_verify(env, pub)                        # signature valid over PAE
    stmt = json.loads(base64.b64decode(env["payload"]))
    assert stmt["predicateType"] == "airbag.dev/heal-attestation/v1"
    assert stmt["subject"][0]["digest"]["sha256"] == hashlib.sha256(blob).hexdigest()  # --check-claims
