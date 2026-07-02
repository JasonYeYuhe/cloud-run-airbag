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

import hashlib
import json

from .report import _recovery_seconds


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
