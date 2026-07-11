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


def _canon(obj) -> str:
    """The one canonicalization used everywhere a digest is computed (byte-identical to the auditor's
    verify._canonical and the Explorer's JS canonicalizer — the parity gate enforces this)."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def build(rec: dict) -> dict:
    """Build the canonical proof bundle + its content digest from a persisted incident record."""
    events = rec.get("events", []) or []
    d = rec.get("decision") or {}
    detection = _stage(events, "ANALYZED", ("verdict", "reason", "signals", "rate"))
    bundle = {
        "bundle_version": BUNDLE_VERSION,
        "incident_id": rec.get("incident_id"),
        "service": rec.get("service"),
        "status": rec.get("status"),
        "decision": {k: d.get(k) for k in ("action", "confidence", "reasoning", "_source")},
        "detection": detection,
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
    # v6 Phase 1.2 borrow (§1b.3 #7, intent binding): a sha256 over the TRIGGERING signal evidence (the
    # ANALYZED multi-signal verdict + the cited evidence), so a signed heal can't be replayed as the
    # proof for a differently-triggered incident. Presence-keyed on the detection (always present on the
    # alert path); it lands AFTER bundle_version, which already made a fresh build differ from an old one
    # — presence-keying alone can't preserve prior bytes here because the evidence is always present.
    if detection is not None:
        bundle["trigger_evidence_digest"] = "sha256:" + hashlib.sha256(
            _canon({"detection": detection, "evidence": d.get("evidence")}).encode("utf-8")).hexdigest()
    # v6 Phase 1.2 borrow (§1b.3 #6, SLSA-style split): make the LLM-QUARANTINE VISIBLE in the
    # attestation. externalParameters = what the advisory tier (Gemini/ADK) SUGGESTED — untrusted input;
    # internalParameters = what the DETERMINISTIC FSM resolved/clamped — the rollback TARGET the LLM
    # never picks (with how it was chosen + whether the FSM OVERRODE an LLM target) plus the autonomy
    # ceiling. Presence-keyed on a decision (a no-decision/sparse bundle stays as-is).
    if d:
        sug = d.get("_suggested") or {}   # pristine pre-validation proposal; fall back for pre-v6 records
        bundle["externalParameters"] = {
            "action": sug.get("action", d.get("action")), "confidence": sug.get("confidence", d.get("confidence")),
            "reasoning": sug.get("reasoning", d.get("reasoning")), "source": d.get("_source")}
        bundle["internalParameters"] = {
            "action": d.get("action"),   # the FSM-RESOLVED action (differs from external on a promote/withhold)
            "rollback_revision": d.get("rollback_revision"), "bad_revision": d.get("bad_revision"),
            "target_source": d.get("_target_source"), "target_overridden": d.get("_target_overridden"),
            "promoted": d.get("_promoted"), "autonomy_level": rec.get("autonomy")}
    canonical = _canon(bundle)
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


def build_dsse_envelope(signed: dict) -> dict | None:
    """Build a DSSE in-toto heal-attestation to emit BESIDE the legacy envelope (never INSIDE it — the
    legacy `signed` dict is not mutated). The payload IS an in-toto Statement whose predicate is the
    SAME signed bundle and whose subject binds the bundle's canonical digest; the DSSE signature is a
    SECOND Cloud KMS asymmetricSign over sha256(PAE) (both terminal-stamp signs are wall-clock bounded,
    R1 #6). cosign `verify-blob-attestation` accepts it (gated in CI). Fail-open: returns None on a bad
    input or ANY error, so a DSSE hiccup never touches the legacy envelope or blocks the heal — this
    function MUST NEVER raise, so the caller's single incidents.record still persists the legacy proof."""
    try:
        from . import dsse
        bundle = signed.get("bundle")
        digest = signed.get("digest") or ""
        if not isinstance(bundle, dict) or not digest.startswith("sha256:"):
            return None
        # signer = the bounded KMS sign_digest, reused as the DSSE signer over "sha256:<hex of PAE>"
        return dsse.build_dsse(bundle, bundle.get("incident_id"), digest.split(":", 1)[-1], signer=sign_digest)
    except Exception as e:  # noqa: BLE001 — FAIL-OPEN: a DSSE build error must never lose the legacy proof
        log.warning("DSSE envelope build failed (%s); degrading to the legacy envelope only", e)
        return None
