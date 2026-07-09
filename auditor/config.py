"""Airbag Auditor — configuration (env-driven). Service tier: stdlib ONLY here, no agent code
(denylist-guarded). The PINNED identities are the load-bearing config a judge can inspect: the
agent's committed pubkey is the authoritative offline trust anchor (§8 Q1 default), and the expected
agent key resource name is the signer identity the auditor refuses to deviate from.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

log = logging.getLogger("airbag.auditor.config")

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parent


def _env_float(name: str, default: float | None) -> float | None:
    """Parse a float env var; on a malformed value LOG loudly and fall back to the default rather than
    raise at import (a deploy-time typo must NEVER crash-loop the independent watchdog — its whole
    thesis is fail-open / never-down; a bad value degrades one knob, it doesn't take the auditor off)."""
    v = os.environ.get(name, "").strip()
    if not v:
        return default
    try:
        return float(v)
    except ValueError:
        log.warning("auditor: ignoring malformed %s=%r; using %r", name, v, default)
        return default


def _env_int(name: str, default: int) -> int:
    v = os.environ.get(name, "").strip()
    if not v:
        return default
    try:
        return int(v)
    except ValueError:
        log.warning("auditor: ignoring malformed %s=%r; using %r", name, v, default)
        return default

# --- the agent we audit -----------------------------------------------------------------------------
# Its public base URL (serves GET /incidents and GET /incidents/{id}/proof — no agent change needed).
AGENT_PROOF_URL = os.environ.get("AIRBAG_AGENT_PROOF_URL", "").rstrip("/")

# The agent's heal-proof signer, PINNED: the committed PEM is the authoritative offline anchor and the
# expected cryptoKeyVersions/N resource name is the identity the auditor pins (a valid signature from
# any other key version FAILs attestation). Both overridable, but the committed defaults ARE the live
# agent's identity (public info — a key resource name + a public key, never a secret).
AGENT_PUBKEY_PEM_PATH = os.environ.get(
    "AIRBAG_AGENT_PUBKEY_PEM", str(_REPO / "scripts" / "airbag-proof-pubkey.pem"))
EXPECTED_AGENT_KEY = os.environ.get(
    "AIRBAG_EXPECTED_AGENT_KEY",
    "projects/airbag-hack-260628/locations/asia-northeast1/keyRings/airbag/"
    "cryptoKeys/airbag-proof/cryptoKeyVersions/1")

# --- the auditor's OWN independent counter-sign identity (distinct key; NEVER airbag-proof) ---------
# Empty on the floor / in dev -> attestations are emitted UNSIGNED (fail-open); the real airbag-auditor
# key + committed scripts/auditor-pubkey.pem land at deploy (infra/auditor-kms-setup.sh).
AUDITOR_KMS_KEY = os.environ.get("AIRBAG_AUDITOR_KMS_KEY", "")
AUDITOR_PUBKEY_PEM_PATH = os.environ.get(
    "AIRBAG_AUDITOR_PUBKEY_PEM", str(_REPO / "scripts" / "auditor-pubkey.pem"))

# Post-cutover DEGRADED signal: an UNSIGNED incident whose activity time >= this cutover instant is a
# strip/hiccup (DEGRADED), not a legitimate pre-4.2 INTEGRITY-ONLY. None on the floor (Phase 3's
# registry feeds the active key's not_before); a numeric epoch string enables it. A malformed value
# LOGS + disables DEGRADED (keeps the auditor up) rather than crash-looping it.
SIGNED_NOT_BEFORE: float | None = _env_float("AIRBAG_SIGNED_NOT_BEFORE", None)

# --- poll cadence / limits --------------------------------------------------------------------------
POLL_INTERVAL_S = _env_float("AIRBAG_POLL_INTERVAL_S", 8.0) or 8.0          # 5-10s -> flip in one beat
MAX_INCIDENTS = _env_int("AIRBAG_MAX_INCIDENTS", 25)
HTTP_TIMEOUT_S = _env_float("AIRBAG_HTTP_TIMEOUT_S", 10.0) or 10.0
# Bound a HOSTILE agent's proof responses (an untrusted party the auditor exists to police): a total
# per-fetch wall-clock deadline (defeats a slow-drip that keeps resetting the per-read timeout) and a
# max body size (a giant/streamed body must not OOM the out-of-band watchdog).
FETCH_DEADLINE_S = _env_float("AIRBAG_FETCH_DEADLINE_S", 20.0) or 20.0
MAX_BODY_BYTES = _env_int("AIRBAG_MAX_BODY_BYTES", 5_000_000)               # a proof bundle is ~3 KB


def agent_pubkey_pem() -> bytes | None:
    p = Path(AGENT_PUBKEY_PEM_PATH)
    return p.read_bytes() if p.exists() else None
