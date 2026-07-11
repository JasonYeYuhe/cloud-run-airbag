"""Tamper-evident incident proof bundle (v3 Phase 3).

A canonical, machine-readable stitch of an incident's evidence — the decision, the detection signals
(multi-signal verdict + per-detector breakdown), the causal pre-check, the recovery proof, the fix
PR, and the full FSM transition log — plus a **sha256 content DIGEST**.

HONEST framing: the digest proves INTEGRITY (the bundle wasn't altered vs the digest — an auditor or
another agent recomputes sha256 over the canonical bundle and compares). It is NOT a cryptographic
SIGNATURE — there is no key and it makes no authorship claim; a WIF/KMS-signed proof is a future step.
So this is "tamper-evident (content digest)", not "cryptographically signed". Deterministic + LLM-free.
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import time

import httpx

from . import config
from .report import _recovery_seconds

log = logging.getLogger("airbag.proof")


def _stage(events, stage, keys):
    e = next((x for x in events if x.get("stage") == stage), None)
    return {k: e.get(k) for k in keys} if e else None


# v6 Phase 1.2 (Round 2 #6/#20): a PERMANENT, self-describing in-band type tag on EVERY heal bundle —
# the heal-side analogue of the auditor's ATTESTATION_VERSION ("airbag.attestation/v1"). NOT flag-gated
# and NOT keyed-on-presence: it is a schema field on every built bundle, so a registry-driven verify
# surface (Phase 3) can match artifact type (heal-proof vs attestation) against the resolved key's role
# and refuse a counter-signed attestation re-wrapped as a "heal". A re-healed proof therefore gains new
# bytes vs an OLD stored bundle — which is why the guard is "no deploy before video", NOT "flag-off
# byte-identical": already-STORED proofs (committed fixtures + demo snapshots) are served verbatim by
# /incidents/{id}/proof before build() is ever called, so they keep verifying unchanged.
BUNDLE_VERSION = "airbag.heal/v1"


def build(rec: dict) -> dict:
    """Build the canonical proof bundle + its content digest from a persisted incident record."""
    events = rec.get("events", []) or []
    d = rec.get("decision") or {}
    bundle = {
        "bundle_version": BUNDLE_VERSION,
        "incident_id": rec.get("incident_id"),
        "service": rec.get("service"),
        "status": rec.get("status"),
        "decision": {k: d.get(k) for k in ("action", "confidence", "reasoning", "_source")},
        "detection": _stage(events, "ANALYZED", ("verdict", "reason", "signals", "rate")),
        "causal": _stage(events, "CAUSAL", ("verdict", "msg")),
        "reversibility": _stage(events, "REVERSIBILITY",
                                ("verdict", "marker_revision", "target", "marker_value", "msg")),
        "recovery": {
            "error_before": rec.get("error_before"), "error_after": rec.get("error_after"),
            "rolled_back_to": rec.get("rolled_back_to"), "restored_to": rec.get("restored_to"),
            "recovery_seconds": _recovery_seconds(events),
        },
        "fix_pr": rec.get("pr_url"),
        "transitions": [{"stage": e.get("stage"), "ts": e.get("ts")} for e in events],
    }
    # v5 5.3: the revision-delta evidence rides the signed bundle — but ONLY when present. Adding the
    # key unconditionally (value None when absent) would change the canonical JSON + digest for EVERY
    # v4 incident; keying it on presence keeps a flag-off bundle byte-identical to v4.
    if rec.get("revision_delta"):
        bundle["revision_delta"] = rec["revision_delta"]
    canonical = json.dumps(bundle, sort_keys=True, separators=(",", ":"), default=str)
    digest = "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return {"bundle": bundle, "digest": digest,
            "note": "content digest — tamper-evident (recompute sha256 over the canonical bundle to "
                    "verify integrity); NOT a cryptographic signature / authorship claim"}


def _bounded(fn, seconds: float):
    """Run `fn` under a hard wall-clock deadline (Round-1 #6: bound BOTH network calls in the KMS sign
    path — the previously UNBOUNDED `creds.refresh`, AND a TOTAL wall-clock cap over the KMS POST, which
    had a per-op `timeout` but no total deadline, so a multi-phase hang could stall the terminal
    MITIGATED/CLOSED stamp — and the DSSE borrow DOUBLES that terminal-stamp KMS exposure). On timeout
    the caller's `except` catches TimeoutError and fails open; the worker thread is abandoned (never
    joined) so a hung socket can't block a completed heal. Mirrors auditor/attestation.py:_bounded (the
    auditor greenfield already applies R1 #6 — kept as a local copy, never a cross-service import, so
    proof.py's LLM-free import surface and the auditor's independence both stay intact)."""
    import concurrent.futures as _f
    ex = _f.ThreadPoolExecutor(max_workers=1)
    fut = ex.submit(fn)
    try:
        return fut.result(timeout=seconds)
    finally:
        ex.shutdown(wait=False)


def sign_digest(digest: str, *, refresh_timeout_s: float = 10.0,
                kms_timeout_s: float = 15.0) -> dict | None:
    """Sign the bundle's sha256 via Cloud KMS asymmetricSign (EC_SIGN_P256_SHA256) over httpx+ADC.
    Returns a signature envelope, or None on ANY failure (FAIL-OPEN — the caller degrades to the
    digest-only bundle; signing must never block a heal). KMS signs the DIGEST (the raw 32 sha256
    bytes, base64), not the hex string; the offline verifier re-hashes the canonical bundle.

    R1 #6: BOTH network calls are wall-clock bounded — the ADC token refresh (`refresh_timeout_s`) and
    the KMS POST (`kms_timeout_s` as a TOTAL deadline over the per-op httpx timeout) — so a KMS/token
    hang can never extend the terminal stamp. Mandatory before the DSSE borrow doubles the KMS
    exposure. Timeouts are keyword-only so the sole caller (`build_signed`) and the deferred DSSE
    second-sign can override them per call without touching the positional contract."""
    if not (config.PROOF_SIGN and config.KMS_KEY):
        return None
    try:
        from google.auth import default as _adc
        from google.auth.transport.requests import Request as _Req
        raw = bytes.fromhex(digest.split(":", 1)[-1])   # sha256 bytes KMS will sign
        creds, _ = _adc(scopes=["https://www.googleapis.com/auth/cloud-platform"])
        _bounded(lambda: creds.refresh(_Req()), refresh_timeout_s)         # R1 #6: bound the token refresh
        r = _bounded(lambda: httpx.post(                                   # R1 #6: TOTAL wall-clock over the POST
            f"https://cloudkms.googleapis.com/v1/{config.KMS_KEY}:asymmetricSign",
            json={"digest": {"sha256": base64.b64encode(raw).decode()}},
            headers={"Authorization": f"Bearer {creds.token}"},
            timeout=httpx.Timeout(kms_timeout_s)), kms_timeout_s)
        r.raise_for_status()
        sig = r.json().get("signature")   # base64 DER ECDSA signature
        if not sig:
            return None
        return {"algorithm": "EC_SIGN_P256_SHA256", "key": config.KMS_KEY, "signature": sig,
                "signed_at": time.time(),
                "note": "PROVENANCE only — signed by the holder of Airbag's KMS identity; NOT a claim "
                        "the decisions inside are correct. Verify offline: scripts/verify-proof.py"}
    except Exception as e:  # noqa: BLE001 — FAIL-OPEN: a signing failure must never block a heal
        log.warning("proof KMS signing failed (%s); degrading to digest-only", e)
        return None


def build_signed(rec: dict) -> dict:
    """build() PLUS (when AIRBAG_PROOF_SIGN is on) a KMS signature over the canonical bundle's digest.
    Fail-open: a signing failure returns the digest-only bundle unchanged (never blocks a heal)."""
    out = build(rec)
    env = sign_digest(out["digest"])
    if env:
        out["signature"] = env
        out["note"] = "cryptographically SIGNED (Cloud KMS EC_SIGN_P256_SHA256, provenance) + " + out["note"]
    return out
