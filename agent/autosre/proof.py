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


def build(rec: dict) -> dict:
    """Build the canonical proof bundle + its content digest from a persisted incident record."""
    events = rec.get("events", []) or []
    d = rec.get("decision") or {}
    bundle = {
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
    canonical = json.dumps(bundle, sort_keys=True, separators=(",", ":"), default=str)
    digest = "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return {"bundle": bundle, "digest": digest,
            "note": "content digest — tamper-evident (recompute sha256 over the canonical bundle to "
                    "verify integrity); NOT a cryptographic signature / authorship claim"}


def sign_digest(digest: str) -> dict | None:
    """Sign the bundle's sha256 via Cloud KMS asymmetricSign (EC_SIGN_P256_SHA256) over httpx+ADC.
    Returns a signature envelope, or None on ANY failure (FAIL-OPEN — the caller degrades to the
    digest-only bundle; signing must never block a heal). KMS signs the DIGEST (the raw 32 sha256
    bytes, base64), not the hex string; the offline verifier re-hashes the canonical bundle."""
    if not (config.PROOF_SIGN and config.KMS_KEY):
        return None
    try:
        from google.auth import default as _adc
        from google.auth.transport.requests import Request as _Req
        raw = bytes.fromhex(digest.split(":", 1)[-1])   # sha256 bytes KMS will sign
        creds, _ = _adc(scopes=["https://www.googleapis.com/auth/cloud-platform"])
        creds.refresh(_Req())
        r = httpx.post(f"https://cloudkms.googleapis.com/v1/{config.KMS_KEY}:asymmetricSign",
                       json={"digest": {"sha256": base64.b64encode(raw).decode()}},
                       headers={"Authorization": f"Bearer {creds.token}"}, timeout=15.0)
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
