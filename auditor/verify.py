"""Airbag Auditor — independent verification kernel (v6 Phase 1.1). ZERO agent imports.

This is the crypto heart of the SECOND, adversarially-independent Cloud Run service. It lifts
`scripts/verify-proof.py:verify()` VERBATIM as its kernel (`_verify_kernel` below — the same two
checks, byte-for-byte behaviour, pinned by `auditor/tests/test_verify.py::test_kernel_parity_with_the_shipped_verifier`)
and adds the ONE thing the stock verifier cannot do — the differentiator the whole marquee rests on:

    a PINNED expected-signer check. `verify-proof.py:verify()` only *ECHOES* the envelope's
    `signature.key`; a second, independent auditor must REFUSE a cryptographically-valid signature
    that claims an UNEXPECTED signer identity. Separation of duties enforced by the crypto path, not
    a bolt-on string compare a refactor could drop.

Direction of trust (V6_VISION Round-3 #4), stated once and load-bearing: we verify against the
CONFIGURED key/PEM (the offline-committed `airbag-proof-pubkey.pem` + the expected
`cryptoKeyVersions/N` resource name) — NEVER against a key resolved FROM the envelope's
`signature.key`, which is UNSIGNED metadata (it lives in the outer envelope, outside the signed
canonical bundle, so an attacker can rewrite it without breaking the signature). The envelope's key
field is compared-TO, never resolved-FROM; and we REPORT the configured identity as the verified
signer, never the envelope's claim.

LLM-FREE by construction: stdlib + `cryptography` ONLY. `auditor/tests/test_auditor_invariant.py`
(Phase 1.3) enforces the import allowlist as an AST check, proving the "zero agent imports" property.
"""
from __future__ import annotations

import base64
import hashlib
import json

# Honest tri-state (+DEGRADED). Plain strings so an attestation serialises trivially and a judge
# reads the exact word off the auditor card. NEVER "verified correct" — provenance + integrity only.
SIGNED_VERIFIED = "SIGNED-VERIFIED"   # integrity_ok AND signature_ok AND the claimed signer == pinned
INTEGRITY_ONLY = "INTEGRITY-ONLY"     # legitimately unsigned (pre-4.2); integrity recomputes, no sig
DEGRADED = "DEGRADED"                 # unsigned BUT a signature was EXPECTED (post-cutover strip/hiccup)
FAIL = "FAIL"                         # integrity broke, OR sig present but invalid, OR signer mismatch


def _canonical(bundle: dict) -> str:
    # MUST match autosre.proof.build's canonicalization EXACTLY. `ensure_ascii` defaults True, so
    # non-ASCII (em-dashes in reasoning strings) canonicalises to \uXXXX escapes — same as the agent.
    return json.dumps(bundle, sort_keys=True, separators=(",", ":"), default=str)


def _verify_kernel(proof: dict, pem_bytes: bytes | None) -> dict:
    """LIFTED VERBATIM from scripts/verify-proof.py:verify() — the two independent checks:
      1. INTEGRITY — recompute sha256 over the canonical bundle, compare to the claimed digest.
      2. PROVENANCE — verify the KMS EC_SIGN_P256_SHA256 signature with the supplied public key.
    Returns {integrity_ok, signature_ok, signed, messages}; signature_ok is None for an unsigned
    bundle or when no key was supplied. Pure — no I/O. Kept behaviour-identical to the shipped script
    (parity test) so the auditor is a genuine independent re-run, not a divergent second opinion."""
    msgs: list[str] = []
    bundle = proof.get("bundle") or {}
    claimed = proof.get("digest", "")
    recomputed = "sha256:" + hashlib.sha256(_canonical(bundle).encode("utf-8")).hexdigest()
    integrity_ok = (recomputed == claimed)
    msgs.append(f"INTEGRITY {'OK' if integrity_ok else 'FAIL'}: {claimed}"
                + ("" if integrity_ok else f" (recomputed {recomputed})"))

    sig = proof.get("signature")
    signature_ok: bool | None = None
    if not sig:
        msgs.append("UNSIGNED: digest-only bundle (no KMS signature to verify)")
    elif not pem_bytes:
        msgs.append("SIGNED but no public key supplied — provenance NOT checked")
    else:
        try:
            from cryptography.hazmat.primitives import hashes, serialization
            from cryptography.hazmat.primitives.asymmetric import ec
            pub = serialization.load_pem_public_key(pem_bytes)
            der = base64.b64decode(sig["signature"])
            pub.verify(der, _canonical(bundle).encode("utf-8"), ec.ECDSA(hashes.SHA256()))
            signature_ok = True
            msgs.append(f"SIGNATURE OK: provenance verified ({sig.get('algorithm')}, key {sig.get('key')})")
        except Exception as e:  # noqa: BLE001
            signature_ok = False
            msgs.append(f"SIGNATURE FAIL: {type(e).__name__}: {e}")

    return {"integrity_ok": integrity_ok, "signature_ok": signature_ok,
            "signed": bool(sig), "messages": msgs}


def _incident_ts(bundle: dict) -> float | None:
    """The incident's activity time = the latest FSM transition timestamp inside the (integrity-covered)
    bundle. Used only to decide `signed_expected` for the DEGRADED classification. Honest caveat: on an
    UNSIGNED bundle a signature-stripping attacker also controls these timestamps (they are only
    integrity-consistent with the recomputed digest, not authentic), so DEGRADED is a best-effort
    signal — the genuine anti-suppression teeth arrive with Phase 2's counter-signed log."""
    ts = [t.get("ts") for t in bundle.get("transitions", []) if isinstance(t.get("ts"), (int, float))]
    return max(ts) if ts else None


def attest(proof: dict, *, expected_pem: bytes | None, expected_key: str | None,
           signed_not_before: float | None = None) -> dict:
    """Independently verify a PUBLISHED proof bundle against a PINNED signer identity and return an
    honest tri-state verdict. This is the auditor's verify path — the counter-signed attestation
    envelope (Phase 1.2) wraps this verdict; the fetch-context binding (Phase 1.2) is layered on top.

    Args:
      expected_pem: the CONFIGURED public key bytes (the offline-committed anchor). Provenance is
        verified against THIS — never a key resolved from the envelope.
      expected_key: the CONFIGURED expected `cryptoKeyVersions/N` resource name. The envelope's
        `signature.key` is compared to this; a valid signature claiming a DIFFERENT key FAILs.
      signed_not_before: if set, an unsigned bundle whose incident time is >= this cutover instant is
        DEGRADED (a signature was expected but is absent — strip or KMS hiccup), not INTEGRITY-ONLY.
        Phase 3's registry feeds the active key's `not_before` here; None (floor default) => an
        unsigned bundle is always the honest pre-4.2 INTEGRITY-ONLY.

    Returns a verdict dict: {tri_state, integrity_ok, signature_ok, signed, signer_pinned,
    expected_key, verified_signer, signed_expected, messages}. `verified_signer` is the CONFIGURED
    identity (reported only when SIGNED-VERIFIED), never the envelope's self-asserted key.
    """
    k = _verify_kernel(proof, expected_pem)
    integrity_ok = k["integrity_ok"]
    signature_ok = k["signature_ok"]
    signed = k["signed"]
    msgs = list(k["messages"])

    # A hostile/garbled published proof may carry a truthy-but-non-dict `signature` (a JSON string,
    # a list) — the kernel already classifies that as signed=True / signature_ok=False (its
    # base64/DER decode raises and is caught). We must NOT be less robust than the kernel we wrap:
    # coerce a non-dict envelope to {} for the key lookup ONLY (the kernel still sees the raw value,
    # so `signed` stays True and the verdict is an honest FAIL — never an uncaught AttributeError).
    raw_sig = proof.get("signature")
    sig = raw_sig if isinstance(raw_sig, dict) else {}
    claimed_key = sig.get("key")
    # PIN: the claimed signer identity must equal the CONFIGURED expected identity. Only meaningful for
    # a signed bundle; the compare is exact-string (resource names are canonical). None when unsigned.
    signer_pinned: bool | None = None
    if signed:
        signer_pinned = bool(expected_key) and (claimed_key == expected_key)
        if not signer_pinned:
            msgs.append(f"SIGNER PIN FAIL: envelope claims key {claimed_key!r}, "
                        f"pinned identity is {expected_key!r}")

    incident_ts = _incident_ts(proof.get("bundle") or {})
    signed_expected = (signed_not_before is not None and incident_ts is not None
                       and incident_ts >= signed_not_before)

    if not integrity_ok:
        tri = FAIL
    elif signed:
        if signature_ok and signer_pinned:
            tri = SIGNED_VERIFIED
            msgs.append(f"ATTEST SIGNED-VERIFIED: provenance confirmed against pinned key {expected_key}")
        else:
            tri = FAIL   # crypto failed (tamper/wrong keypair) OR the signer pin rejected the identity
    else:
        if signed_expected:
            tri = DEGRADED
            msgs.append("ATTEST DEGRADED: a signature was EXPECTED for this incident (post-cutover) "
                        "but the bundle is unsigned — strip or KMS hiccup, distinct from a legitimate "
                        "pre-signing INTEGRITY-ONLY bundle")
        else:
            tri = INTEGRITY_ONLY

    return {
        "tri_state": tri,
        "integrity_ok": integrity_ok,
        "signature_ok": signature_ok,
        "signed": signed,
        "signer_pinned": signer_pinned,
        "expected_key": expected_key,
        "verified_signer": expected_key if tri == SIGNED_VERIFIED else None,
        "signed_expected": signed_expected,
        "messages": msgs,
    }
