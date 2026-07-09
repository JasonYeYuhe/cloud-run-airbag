"""Airbag Auditor — counter-signed attestation + fetch-context binding (v6 Phase 1.2).

The auditor, having independently verified a PUBLISHED heal proof (verify.attest), emits its OWN
counter-signed attestation: a different agent, with a DISTINCT KMS identity, vouching for the heal —
trusting neither Airbag's word nor its own echo.

Load-bearing properties:
- READ-ONLY / out-of-band. This module NEVER writes to the heal FSM (no write path exists in the
  auditor), so it structurally cannot block a heal.
- FAIL-OPEN. A counter-signing failure surfaces as an UNSIGNED ("unattested") attestation, never a
  raised exception that stops the audit loop.
- BINDS THE FETCH CONTEXT (Round-3 #1): the sha256 of the RAW fetched bytes, the agent URL, the
  requested incident id, the HTTP status, AND a `bundle.incident_id == requested-id` check — so a
  cryptographically-valid bundle for incident A can never answer a query for incident B (that FAILs).
- CARRIES AN IN-BAND TYPE TAG (Round-2 #6): `attestation_version`, so an attestation can never be
  replayed as a heal proof on a registry-driven verify surface (Phase 3 enforces role/type; the tag
  is emitted here).
- OFFLINE-VERIFIABLE BY THE SAME KERNEL. The counter-signed envelope has the exact {bundle, digest,
  signature} shape verify.attest consumes, so `auditor/verify.py` re-verifies the attestation against
  the auditor's committed pubkey — the counter-signature is not theatre.

NOT allowlist-pure: the KMS counter-sign path needs httpx + google-auth (function-local). This module
is guarded by the DENYLIST half of test_auditor_invariant.py (no agent / no LLM imports), NOT the
verify.py allowlist. The pure crypto kernel it reuses (verify.py) stays allowlist-pure.
"""
from __future__ import annotations

import base64
import hashlib
import logging
import time
from typing import Callable

import verify   # sibling auditor module — the pure, allowlist-guarded kernel (NOT agent code)

log = logging.getLogger("airbag.auditor.attestation")

# In-band type tag: domain separation so a counter-signed attestation can never be re-wrapped as a
# SIGNED-VERIFIED "heal" on any registry-driven verify surface (Round-2 #6). Heal bundles carry the
# analogous `bundle_version` (agent-side, Phase 1.2 proof.py — deferred while agent-side is frozen).
ATTESTATION_VERSION = "airbag.attestation/v1"

# Signer contract: a callable digest("sha256:...") -> signature-envelope | None (None = fail-open).
Signer = Callable[[str], "dict | None"]


def _raw_digest(raw_bytes: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw_bytes).hexdigest()


def build_attestation(proof: dict, raw_bytes: bytes, verdict: dict, *, agent_url: str,
                      requested_incident_id: str, http_status: int, verified_at: float) -> dict:
    """Build the (unsigned) attestation binding the verdict to the fetch context. If the fetched
    bundle's incident_id does not match the REQUESTED id, the attested tri_state is forced to FAIL
    (a valid bundle for the wrong incident is not an answer to this query)."""
    proof = proof if isinstance(proof, dict) else {}   # hostile published JSON must never raise here
    bundle = proof.get("bundle")
    if not isinstance(bundle, dict):
        bundle = {}
    bundle_incident_id = bundle.get("incident_id")
    id_match = (bundle_incident_id == requested_incident_id)
    tri = verdict["tri_state"] if id_match else verify.FAIL
    return {
        "attestation_version": ATTESTATION_VERSION,
        "incident_id": bundle_incident_id,
        "tri_state": tri,
        "signed_expected": verdict["signed_expected"],
        "expected_key": verdict["expected_key"],
        # report the CONFIGURED verified signer only when the heal itself is SIGNED-VERIFIED
        "verified_signer": verdict["verified_signer"] if tri == verify.SIGNED_VERIFIED else None,
        "subject_digest": proof.get("digest"),            # the heal bundle's own content digest
        "verified_at": verified_at,
        "fetch": {
            "agent_url": agent_url,
            "requested_incident_id": requested_incident_id,
            "http_status": http_status,
            "raw_fetched_digest": _raw_digest(raw_bytes),  # binds the EXACT bytes the auditor received
            "incident_id_match": id_match,
        },
    }


def counter_sign(attestation: dict, *, signer: Signer) -> dict:
    """Canonicalize + counter-sign the attestation into a {bundle, digest, signature?} envelope the
    same kernel verifies. Fail-open: if `signer` returns None the envelope is emitted UNSIGNED."""
    canonical = verify._canonical(attestation)
    digest = "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    env = {
        "bundle": attestation,
        "digest": digest,
        "note": "auditor COUNTER-SIGNED attestation — an independent second agent's verdict on a "
                "PUBLISHED heal proof; PROVENANCE of the verdict, NOT a claim the heal's decisions "
                "are correct. Verify offline: auditor/verify.py against scripts/auditor-pubkey.pem",
    }
    try:
        sig = signer(digest)
    except Exception as e:  # noqa: BLE001 — FAIL-OPEN: a signer that RAISES must not stop the audit
        log.warning("attestation counter-sign raised (%s); emitting unsigned attestation", e)
        sig = None
    if sig:
        env["signature"] = sig
    return env


def verify_and_attest(proof: dict, raw_bytes: bytes, *, expected_pem: bytes | None,
                      expected_key: str | None, agent_url: str, requested_incident_id: str,
                      http_status: int, verified_at: float, signer: Signer,
                      signed_not_before: float | None = None) -> dict:
    """Full out-of-band audit of ONE published proof: verify (pinned) -> bind fetch context ->
    counter-sign. Returns the counter-signed attestation envelope (unsigned on fail-open signing)."""
    verdict = verify.attest(proof, expected_pem=expected_pem, expected_key=expected_key,
                            signed_not_before=signed_not_before)
    attestation = build_attestation(proof, raw_bytes, verdict, agent_url=agent_url,
                                    requested_incident_id=requested_incident_id,
                                    http_status=http_status, verified_at=verified_at)
    return counter_sign(attestation, signer=signer)


def is_attestation(payload: dict) -> bool:
    """Domain-separation predicate: is this signed payload an auditor attestation (vs a heal bundle)?
    A registry-driven verify surface (Phase 3) uses this to refuse cross-type replay."""
    return isinstance(payload, dict) and payload.get("attestation_version") == ATTESTATION_VERSION


def _bounded(fn, seconds: float):
    """Run `fn` under a hard wall-clock deadline (Round-1 #6: bound BOTH network calls in the sign
    path). On timeout the caller's except catches TimeoutError and fails open; the worker thread is
    abandoned (never joined) so a hung socket can't stall the out-of-band audit loop."""
    import concurrent.futures as _f
    ex = _f.ThreadPoolExecutor(max_workers=1)
    fut = ex.submit(fn)
    try:
        return fut.result(timeout=seconds)
    finally:
        ex.shutdown(wait=False)


def sign_digest_kms(digest: str, key_resource: str, *, refresh_timeout_s: float = 10.0,
                    sign_timeout_s: float = 10.0) -> dict | None:
    """The AUDITOR's KMS counter-sign over Cloud KMS asymmetricSign (EC_SIGN_P256_SHA256), using the
    auditor's OWN key (`key_resource` — NEVER airbag-proof). Returns a signature envelope or None on
    ANY failure (FAIL-OPEN). BOTH network calls are bounded from the start (Round-1 #6, applied to the
    auditor greenfield): the ADC token refresh AND the KMS POST. KMS signs the raw 32 sha256 bytes."""
    try:
        import httpx
        from google.auth import default as _adc
        from google.auth.transport.requests import Request as _Req
        raw = bytes.fromhex(digest.split(":", 1)[-1])
        creds, _ = _adc(scopes=["https://www.googleapis.com/auth/cloud-platform"])
        _bounded(lambda: creds.refresh(_Req()), refresh_timeout_s)      # bounded token refresh
        r = httpx.post(f"https://cloudkms.googleapis.com/v1/{key_resource}:asymmetricSign",
                       json={"digest": {"sha256": base64.b64encode(raw).decode()}},
                       headers={"Authorization": f"Bearer {creds.token}"},
                       timeout=httpx.Timeout(sign_timeout_s))            # bounded KMS POST
        r.raise_for_status()
        sig = r.json().get("signature")
        if not sig:
            return None
        return {"algorithm": "EC_SIGN_P256_SHA256", "key": key_resource, "signature": sig,
                "signed_at": time.time(),
                "note": "auditor attestation counter-signature (PROVENANCE of the verdict)"}
    except Exception as e:  # noqa: BLE001 — FAIL-OPEN: a signing failure must never stop the audit
        log.warning("auditor KMS counter-sign failed (%s); emitting unsigned attestation", e)
        return None
