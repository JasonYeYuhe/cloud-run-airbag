"""DSSE + in-toto Statement construction for a heal-attestation (v6 Phase 1.2 borrow, §1b.3 #4/#5).

Deterministic + LLM-free. Builds a DSSE envelope whose PAYLOAD **is** an in-toto Statement, so
`cosign verify-blob-attestation` accepts an Airbag heal on camera (a raw-bundle payload would fail
cosign by design — cosign parses the payload as a Statement and matches the blob digest against its
`subject`). The DSSE signature is ECDSA-P256 over `PAE(payloadType, statement_bytes)`; because
Cloud KMS `EC_SIGN_P256_SHA256` signs a DIGEST, the live path signs `sha256(PAE)`, which equals
`Sign(PAE)` for an ECDSA-P256/SHA256 verifier (cosign). The SAME construction serves the cosign-in-CI
golden (local EC key) and the live KMS path (A5) — signing is INJECTED as a callback so this module
never imports httpx/KMS/the LLM.

This module is the heal-side analogue of `auditor/attestation.py` (which counter-signs the verdict);
it joins `autosre/_action_files()` because it assembles part of the signed proof artifact and must
stay LLM-free (Round 2 #24, same discipline as proof.py).

Standards references (verified 2026-07-11 against primary sources):
- DSSE PAE: secure-systems-lab/dsse protocol.md — `"DSSEv1" SP LEN(type) SP type SP LEN(body) SP body`,
  LEN = ASCII decimal of the byte length, signature computed directly over the PAE bytes.
- in-toto Statement + cosign: cosign `verify-blob-attestation --type <predicateType> --check-claims`
  matches sha256(blob) against `subject[].digest.sha256`; payloadType `application/vnd.in-toto+json`.
"""
from __future__ import annotations

import base64
import hashlib
import json

# DSSE / in-toto constants — cosign matches on these EXACTLY.
PAYLOAD_TYPE = "application/vnd.in-toto+json"
STATEMENT_TYPE = "https://in-toto.io/Statement/v1"
PREDICATE_TYPE = "airbag.dev/heal-attestation/v1"


def pae(payload_type: bytes, body: bytes) -> bytes:
    """DSSE Pre-Authentication Encoding (protocol.md):
        PAE = "DSSEv1" SP LEN(type) SP type SP LEN(body) SP body
    LEN is the ASCII decimal byte length (bytes `%`-formatting yields exactly that). The signature is
    computed over these bytes directly; do NOT pre-hash here (the SIGNER pre-hashes for KMS)."""
    return b"DSSEv1 %d %s %d %s" % (len(payload_type), payload_type, len(body), body)


def in_toto_statement(bundle: dict, incident_id, subject_digest_hex: str,
                      predicate_type: str = PREDICATE_TYPE) -> dict:
    """An in-toto Statement whose `predicate` IS the heal bundle and whose single `subject` binds the
    canonical bundle's sha256 (what cosign `--check-claims` matches against the verified blob)."""
    return {
        "_type": STATEMENT_TYPE,
        "subject": [{"name": incident_id or "airbag-heal",
                     "digest": {"sha256": subject_digest_hex}}],
        "predicateType": predicate_type,
        "predicate": bundle,
    }


def statement_bytes(statement: dict) -> bytes:
    """Serialize the Statement to the exact bytes the DSSE payload carries AND that PAE covers. Sorted,
    compact, ascii-escaped — deterministic so the golden fixture and the live emit are reproducible."""
    return json.dumps(statement, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")


def dsse_envelope(body: bytes, sig_b64: str, keyid: str = "") -> dict:
    """A DSSE envelope: base64 payload + payloadType + one signature (the shape cosign consumes)."""
    return {
        "payloadType": PAYLOAD_TYPE,
        "payload": base64.b64encode(body).decode("ascii"),
        "signatures": [{"keyid": keyid, "sig": sig_b64}],
    }


def pae_digest(bundle: dict, incident_id, subject_digest_hex: str,
               predicate_type: str = PREDICATE_TYPE) -> tuple[bytes, str]:
    """Build the Statement bytes and return `(statement_bytes, "sha256:<hex of PAE>")` — the digest the
    signer signs. Factored out so a caller can sign without re-deriving the PAE."""
    body = statement_bytes(in_toto_statement(bundle, incident_id, subject_digest_hex, predicate_type))
    pae_bytes = pae(PAYLOAD_TYPE.encode("ascii"), body)
    return body, "sha256:" + hashlib.sha256(pae_bytes).hexdigest()


def build_dsse(bundle: dict, incident_id, subject_digest_hex: str, *, signer,
               predicate_type: str = PREDICATE_TYPE) -> dict | None:
    """Assemble a signed DSSE heal-attestation envelope, or None on a fail-open signer.

    `signer(digest)` takes `"sha256:<hex>"` (the sha256 of PAE) and returns a signature envelope
    `{"signature": <base64 DER ECDSA>, "key": <resource>, ...}` or None — the SAME contract as
    `proof.sign_digest`, so A5 passes the KMS signer and the CI golden passes a local EC signer. Returns
    None (fail-open) if the signer declines, so the caller degrades to the legacy envelope untouched."""
    body, digest = pae_digest(bundle, incident_id, subject_digest_hex, predicate_type)
    sig_env = signer(digest)
    if not sig_env or not sig_env.get("signature"):
        return None
    return dsse_envelope(body, sig_env["signature"], keyid=sig_env.get("key", "") or "")
