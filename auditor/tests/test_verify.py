"""Phase 1.1 TDD proof set — the auditor's verify core + signer PIN + honest tri-state.

Runs as `cd auditor && ../.venv-demo/bin/python -m pytest -q` (cwd on sys.path -> `import verify`).
Self-contained: builds + locally signs bundles with a throwaway EC-P256 key (mimicking Cloud KMS
EC_SIGN_P256_SHA256) so no agent code is imported — the same independence the marquee claims. One
test additionally re-verifies the REAL committed KMS-signed fixture against the committed PEM.
"""
import base64
import hashlib
import importlib.util
import json
from pathlib import Path

import verify

_REPO = Path(__file__).resolve().parents[2]
_EXPECTED_KEY = ("projects/airbag-hack-260628/locations/asia-northeast1/keyRings/airbag/"
                 "cryptoKeys/airbag-proof/cryptoKeyVersions/1")
_WRONG_VERSION_KEY = _EXPECTED_KEY.replace("cryptoKeyVersions/1", "cryptoKeyVersions/2")

# load the SHIPPED offline verifier as a module so the parity test pins the REAL verify() behaviour
_VP_PATH = _REPO / "scripts" / "verify-proof.py"
_spec = importlib.util.spec_from_file_location("verify_proof_shipped", _VP_PATH)
verify_proof_shipped = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(verify_proof_shipped)


def _bundle(ts: float = 1_783_094_600.0) -> dict:
    # The em-dash in `reasoning` is deliberate: it forces ensure_ascii=True canonicalisation (\uXXXX),
    # the exact trap a naive second canonicalizer falls into. `rate":0.0` guards float-token fidelity.
    return {
        "incident_id": "inc-test", "service": "airbag-target", "status": "mitigated",
        "decision": {"action": "ROLLBACK", "confidence": 0.7,
                     "reasoning": "statistical gate FAIL — latency 4/4 windows over SLO",
                     "_source": "heuristic"},
        "detection": {"verdict": "FAIL", "reason": "latency", "rate": 0.0},
        "recovery": {"error_before": 0.12, "error_after": 0.0,
                     "rolled_back_to": "airbag-target-00024", "restored_to": None,
                     "recovery_seconds": 117.2},
        "transitions": [{"stage": "RUN_START", "ts": ts - 100.0}, {"stage": "MITIGATED", "ts": ts}],
    }


def _sign(bundle: dict, *, key_name: str = _EXPECTED_KEY):
    """Mimic Cloud KMS: sign sha256(canonical bundle) with a fresh EC-P256 key. Returns
    (proof_envelope, public_pem). `key_name` is the envelope's self-asserted signer identity."""
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    priv = ec.generate_private_key(ec.SECP256R1())
    pub_pem = priv.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    canonical = json.dumps(bundle, sort_keys=True, separators=(",", ":"), default=str)
    digest = "sha256:" + hashlib.sha256(canonical.encode()).hexdigest()
    der = priv.sign(canonical.encode(), ec.ECDSA(hashes.SHA256()))
    proof = {"bundle": bundle, "digest": digest,
             "signature": {"algorithm": "EC_SIGN_P256_SHA256", "key": key_name,
                           "signature": base64.b64encode(der).decode()}}
    return proof, pub_pem


def _unsigned(bundle: dict) -> dict:
    canonical = json.dumps(bundle, sort_keys=True, separators=(",", ":"), default=str)
    return {"bundle": bundle, "digest": "sha256:" + hashlib.sha256(canonical.encode()).hexdigest()}


# --- SIGNED-VERIFIED --------------------------------------------------------------------------------
def test_valid_signed_is_signed_verified():
    proof, pem = _sign(_bundle())
    v = verify.attest(proof, expected_pem=pem, expected_key=_EXPECTED_KEY)
    assert v["tri_state"] == verify.SIGNED_VERIFIED
    assert v["integrity_ok"] and v["signature_ok"] and v["signer_pinned"]


def test_signed_verified_reports_the_configured_signer_not_the_envelope_claim():
    """Round-3 #4: the reported signer is the CONFIGURED identity, never the envelope's self-claim."""
    proof, pem = _sign(_bundle())
    v = verify.attest(proof, expected_pem=pem, expected_key=_EXPECTED_KEY)
    assert v["tri_state"] == verify.SIGNED_VERIFIED
    assert v["verified_signer"] == _EXPECTED_KEY


def test_real_committed_kms_fixture_is_signed_verified():
    """End-to-end against REAL Cloud KMS output: the committed live latency heal + committed PEM.
    Pin against the HARDCODED expected identity (not the envelope's self-claim) so this test
    independently demonstrates the signer pin; assert the fixture actually carries that key."""
    proof = json.loads((_REPO / "docs" / "proof" / "live-kms-signed-latency-heal.json").read_text())
    pem = (_REPO / "scripts" / "airbag-proof-pubkey.pem").read_bytes()
    assert proof["signature"]["key"] == _EXPECTED_KEY          # drift guard on the committed fixture
    v = verify.attest(proof, expected_pem=pem, expected_key=_EXPECTED_KEY)
    assert v["tri_state"] == verify.SIGNED_VERIFIED
    assert v["verified_signer"] == _EXPECTED_KEY and _EXPECTED_KEY.endswith("cryptoKeyVersions/1")


# --- FAIL: tamper, wrong keypair, and THE NEW pinned-version case -----------------------------------
def test_tamper_inside_bundle_fails():
    proof, pem = _sign(_bundle())
    proof["bundle"]["recovery"]["rolled_back_to"] = "attacker-swapped"   # mutate INSIDE the bundle
    v = verify.attest(proof, expected_pem=pem, expected_key=_EXPECTED_KEY)
    assert v["tri_state"] == verify.FAIL and v["integrity_ok"] is False


def test_wrong_keypair_fails():
    proof, _ = _sign(_bundle())
    _, other_pem = _sign(_bundle())          # a DIFFERENT key's public PEM
    v = verify.attest(proof, expected_pem=other_pem, expected_key=_EXPECTED_KEY)
    assert v["tri_state"] == verify.FAIL and v["signature_ok"] is False


def test_valid_signature_wrong_key_version_fails():
    """THE NEW case (absent from v5's wrong-*keypair* test): the signature verifies against the pinned
    PEM, but the envelope claims cryptoKeyVersions/2 while we pin /1. Because `signature.key` is
    UNSIGNED metadata, editing it does NOT break the crypto — so the PIN is the only thing that catches
    it. Must FAIL despite signature_ok being True."""
    proof, pem = _sign(_bundle(), key_name=_WRONG_VERSION_KEY)
    v = verify.attest(proof, expected_pem=pem, expected_key=_EXPECTED_KEY)
    assert v["signature_ok"] is True            # crypto genuinely verifies
    assert v["signer_pinned"] is False          # but the claimed identity is not the pinned one
    assert v["tri_state"] == verify.FAIL


def test_signed_bundle_without_a_configured_pin_cannot_be_signed_verified():
    """Misconfiguration safety: no pinned identity => cannot reach SIGNED-VERIFIED even if crypto is OK."""
    proof, pem = _sign(_bundle())
    v = verify.attest(proof, expected_pem=pem, expected_key=None)
    assert v["signature_ok"] is True and v["signer_pinned"] is False
    assert v["tri_state"] == verify.FAIL


def test_malformed_nondict_signature_is_fail_not_crash():
    """Confirmed review finding (correctness/major): a truthy non-dict `signature` (string / list /
    number) is a hostile or garbled envelope. The auditor MUST return an honest FAIL verdict, never an
    uncaught exception — it exists precisely to verify UNTRUSTED published proofs. Must be no less
    robust than the kernel it wraps (which classifies the same input as signed=True/signature_ok=False)."""
    _, pem = _sign(_bundle())
    for bad in ("tampered", ["a", "b"], 12345):
        p = _unsigned(_bundle())               # integrity-OK bundle so ONLY the signature is malformed
        p["signature"] = bad
        v = verify.attest(p, expected_pem=pem, expected_key=_EXPECTED_KEY)
        assert v["tri_state"] == verify.FAIL
        assert v["signed"] is True and v["signature_ok"] is False


# --- integrity-FAIL PRECEDENCE: a broken digest must dominate EVERY other branch -------------------
def test_unsigned_tampered_is_fail():
    p = _unsigned(_bundle())
    p["bundle"]["service"] = "attacker-swapped"       # digest no longer matches the mutated bundle
    v = verify.attest(p, expected_pem=None, expected_key=_EXPECTED_KEY)
    assert v["tri_state"] == verify.FAIL and v["integrity_ok"] is False


def test_post_cutover_unsigned_tampered_is_fail_never_degraded():
    """The strip-PLUS-tamper attacker: unsigned, mutated, AND post-cutover (signed_expected True).
    Integrity FAIL must dominate — the verdict is FAIL, never softened to DEGRADED."""
    ts = 1_783_094_600.0
    p = _unsigned(_bundle(ts))
    p["bundle"]["recovery"]["rolled_back_to"] = "attacker"   # break integrity (transitions ts intact)
    v = verify.attest(p, expected_pem=None, expected_key=_EXPECTED_KEY, signed_not_before=ts - 50.0)
    assert v["signed_expected"] is True               # DEGRADED WOULD fire if precedence were wrong
    assert v["tri_state"] == verify.FAIL              # but integrity FAIL wins


def test_signed_tampered_with_wrong_signer_is_fail():
    proof, pem = _sign(_bundle(), key_name=_WRONG_VERSION_KEY)
    proof["bundle"]["service"] = "attacker"           # break integrity AND claim a mismatched key
    v = verify.attest(proof, expected_pem=pem, expected_key=_EXPECTED_KEY)
    assert v["tri_state"] == verify.FAIL and v["integrity_ok"] is False


# --- INTEGRITY-ONLY vs DEGRADED (the honest-unsigned split) -----------------------------------------
def test_pre_cutover_unsigned_is_integrity_only():
    v = verify.attest(_unsigned(_bundle()), expected_pem=None, expected_key=_EXPECTED_KEY)
    assert v["tri_state"] == verify.INTEGRITY_ONLY
    assert v["signature_ok"] is None and v["signed"] is False


def test_post_cutover_unsigned_is_degraded_never_plain_integrity_only():
    """Round-2 #8: an unsigned bundle whose incident time is AFTER the signing cutover is a strip/hiccup
    (DEGRADED), visibly distinct from a legitimate pre-4.2 INTEGRITY-ONLY."""
    ts = 1_783_094_600.0
    v = verify.attest(_unsigned(_bundle(ts)), expected_pem=None, expected_key=_EXPECTED_KEY,
                      signed_not_before=ts - 50.0)   # cutover BEFORE the incident
    assert v["tri_state"] == verify.DEGRADED
    assert v["signed_expected"] is True


def test_unsigned_before_the_cutover_is_integrity_only():
    ts = 1_783_094_600.0
    v = verify.attest(_unsigned(_bundle(ts)), expected_pem=None, expected_key=_EXPECTED_KEY,
                      signed_not_before=ts + 1000.0)  # cutover AFTER the incident -> legitimately unsigned
    assert v["tri_state"] == verify.INTEGRITY_ONLY and v["signed_expected"] is False


def test_unsigned_without_cutover_config_stays_integrity_only():
    """Floor default (no registry not_before): an unsigned bundle is always the honest INTEGRITY-ONLY."""
    v = verify.attest(_unsigned(_bundle()), expected_pem=None, expected_key=_EXPECTED_KEY,
                      signed_not_before=None)
    assert v["tri_state"] == verify.INTEGRITY_ONLY


def test_post_cutover_unsigned_without_timestamps_conservatively_integrity_only():
    """No transition timestamps => cannot PROVE the incident is post-cutover => stay INTEGRITY-ONLY
    (never fabricate a DEGRADED we can't substantiate)."""
    b = _bundle()
    b["transitions"] = []
    v = verify.attest(_unsigned(b), expected_pem=None, expected_key=_EXPECTED_KEY,
                      signed_not_before=1_000.0)
    assert v["tri_state"] == verify.INTEGRITY_ONLY


# --- honesty guardrails -----------------------------------------------------------------------------
def test_tri_state_is_always_one_of_the_four_and_never_claims_correct():
    for proof, pem, key in [(*_sign(_bundle()), _EXPECTED_KEY),
                            (_unsigned(_bundle()), None, _EXPECTED_KEY)]:
        v = verify.attest(proof, expected_pem=pem, expected_key=key)
        assert v["tri_state"] in {verify.SIGNED_VERIFIED, verify.INTEGRITY_ONLY,
                                  verify.DEGRADED, verify.FAIL}
        joined = " ".join(v["messages"]).lower()
        assert "correct" not in joined or "not a claim" in joined  # never asserts decision correctness
    # verified_signer is populated ONLY on SIGNED-VERIFIED
    v_unsigned = verify.attest(_unsigned(_bundle()), expected_pem=None, expected_key=_EXPECTED_KEY)
    assert v_unsigned["verified_signer"] is None


# --- hostile-input robustness: attest must NEVER raise on garbage (confirmed review finding, major) --
def test_hostile_malformed_proof_never_crashes_attest():
    """A hostile PUBLISHED proof must yield an honest verdict, never an uncaught exception — the exact
    suppression vector a compromised agent would use against its own auditor. Covers a non-dict bundle,
    a non-dict proof, and malformed `transitions` (None / string / list-of-non-dicts) — the symmetric
    twin of the non-dict-signature case."""
    hostile = [
        {"bundle": "x", "digest": "sha256:" + "0" * 64},              # bundle a string
        {"bundle": [1, 2], "digest": "sha256:" + "0" * 64},           # bundle a list
        {"bundle": {"transitions": None}, "digest": "sha256:" + "0" * 64},
        {"bundle": {"transitions": "oops"}, "digest": "sha256:" + "0" * 64},
        {"bundle": {"transitions": [1, 2, 3]}, "digest": "sha256:" + "0" * 64},
        ["not", "even", "an", "object"],                              # proof itself a list
        "a bare string",                                              # proof a string
        42,                                                           # proof a number
    ]
    for proof in hostile:
        v = verify.attest(proof, expected_pem=None, expected_key=_EXPECTED_KEY)   # must not raise
        assert v["tri_state"] in {verify.SIGNED_VERIFIED, verify.INTEGRITY_ONLY,
                                  verify.DEGRADED, verify.FAIL}


def test_valid_integrity_bundle_with_malformed_transitions_does_not_crash_degraded_path():
    """Pins the exact crash site: an integrity-VALID unsigned bundle with `transitions: null`, attested
    with a cutover set, reaches _incident_ts on the DEGRADED path. Pre-fix `for t in None` raised."""
    b = {"incident_id": "inc-A", "service": "s", "transitions": None}
    proof = _unsigned(b)
    v = verify.attest(proof, expected_pem=None, expected_key=_EXPECTED_KEY, signed_not_before=1.0)
    assert v["tri_state"] == verify.INTEGRITY_ONLY and v["signed_expected"] is False


# --- parity: the lifted kernel is behaviour-identical to the shipped verifier -----------------------
def test_kernel_parity_with_the_shipped_verifier():
    """`_verify_kernel` must be a genuine VERBATIM lift of scripts/verify-proof.py:verify() — same
    integrity_ok / signature_ok / signed on every input class, or the auditor is a divergent second
    opinion rather than an independent re-run."""
    signed, pem = _sign(_bundle())
    tampered, _ = _sign(_bundle()); tampered["bundle"]["service"] = "x"
    wrong_signed, _ = _sign(_bundle()); _, wrong_pem = _sign(_bundle())
    cases = [
        (signed, pem),                       # valid signed
        (tampered, pem),                     # integrity break
        (wrong_signed, wrong_pem),           # wrong keypair
        (_unsigned(_bundle()), None),        # unsigned
        (signed, None),                      # signed but no key supplied
    ]
    for proof, key_pem in cases:
        mine = verify._verify_kernel(proof, key_pem)
        theirs = verify_proof_shipped.verify(proof, key_pem)
        assert mine["integrity_ok"] == theirs["integrity_ok"]
        assert mine["signature_ok"] == theirs["signature_ok"]
        assert mine["signed"] == theirs["signed"]
