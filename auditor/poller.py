"""Airbag Auditor — the poll + audit loop.

Fetches the agent's PUBLIC published proofs (GET /incidents to list, GET /incidents/{id}/proof to
fetch) and produces counter-signed attestations via attestation.verify_and_attest. READ-ONLY /
out-of-band: the ONLY interaction with the agent is outbound GETs to its public URL — there is no
write path into the agent, so the auditor structurally cannot block a heal.

FAIL-OPEN: an unreachable agent, a non-200 status, or non-JSON garbage yields an HONEST FAIL
attestation (http_status recorded), never an exception that stops the loop. The `fetch` primitive is
injected (fetch(url) -> (raw_bytes, http_status)) so the whole loop is testable without real HTTP.

Service tier (denylist-guarded): imports the pure kernel + counter-signer siblings, httpx
(function-local, prod fetch only), and stdlib — never agent code, never the LLM.
"""
from __future__ import annotations

import json
import logging
from typing import Callable

import attestation

log = logging.getLogger("airbag.auditor.poller")

# fetch(url) -> (raw_bytes, http_status). status 0 == the fetch itself failed (unreachable / timeout).
Fetch = Callable[[str], "tuple[bytes, int]"]


def _list_incident_ids(fetch: Fetch, agent_url: str, limit: int) -> list[str]:
    """GET {agent_url}/incidents?limit=N -> [incident_id, ...] (most-recent first). Fail-open to [].

    Hardened against a HOSTILE agent (the untrusted party the auditor exists to police):
      * incident_id MUST be a str — a list/dict id would later become an unhashable dict key and
        crash the whole poll cycle, permanently blinding the auditor (a trivial suppression attack);
      * the returned list is TRUNCATED to `limit` locally — the ?limit=N query is only a hint the
        agent may ignore, and an oversized list would wedge the sequential poll cycle.
    """
    n = max(0, int(limit))
    try:
        raw, status = fetch(f"{agent_url.rstrip('/')}/incidents?limit={n}")
        if status == 200 and raw:
            obj = json.loads(raw)
            incidents = obj.get("incidents") if isinstance(obj, dict) else None
            if isinstance(incidents, list):
                ids = [i.get("incident_id") for i in incidents
                       if isinstance(i, dict) and isinstance(i.get("incident_id"), str)]
                return ids[:n]                       # enforce the cap locally — never trust the agent
    except Exception as e:  # noqa: BLE001 — listing is best-effort; a failure just yields no work
        log.warning("auditor: list incidents failed (%s)", e)
    return []


def audit_incident(incident_id: str, *, fetch: Fetch, agent_url: str, expected_pem: bytes | None,
                   expected_key: str | None, signer, verified_at: float,
                   signed_not_before: float | None = None) -> dict:
    """Fetch ONE incident's published proof and produce its counter-signed attestation. Fail-open:
    a fetch/parse failure still yields an attestation (FAIL, http_status recorded)."""
    url = f"{agent_url.rstrip('/')}/incidents/{incident_id}/proof"
    try:
        raw, status = fetch(url)
    except Exception as e:  # noqa: BLE001 — an unreachable agent is an honest FAIL, not a crash
        log.warning("auditor: fetch failed for %s (%s)", incident_id, e)
        raw, status = b"", 0
    try:
        proof = json.loads(raw) if raw else None
    except Exception:  # noqa: BLE001 — non-JSON -> None -> verify_and_attest classifies it FAIL
        proof = None
    return attestation.verify_and_attest(
        proof, raw or b"", expected_pem=expected_pem, expected_key=expected_key, agent_url=agent_url,
        requested_incident_id=incident_id, http_status=status, verified_at=verified_at,
        signer=signer, signed_not_before=signed_not_before)


def poll_once(*, fetch: Fetch, agent_url: str, expected_pem: bytes | None, expected_key: str | None,
              signer, now: Callable[[], float], limit: int = 25,
              signed_not_before: float | None = None) -> dict:
    """List incidents and audit each. Returns {incident_id: attestation_envelope}. `now` is injected
    (now() -> epoch float) so audit timestamps are deterministic in tests."""
    out: dict[str, dict] = {}
    for iid in _list_incident_ids(fetch, agent_url, limit):
        try:                                          # defense-in-depth: one poison id can't abort the cycle
            out[iid] = audit_incident(
                iid, fetch=fetch, agent_url=agent_url, expected_pem=expected_pem,
                expected_key=expected_key, signer=signer, verified_at=now(),
                signed_not_before=signed_not_before)
        except Exception as e:  # noqa: BLE001 — never let a single incident kill the whole poll cycle
            log.warning("auditor: audit failed for %r (%s)", iid, e)
    return out


def httpx_fetch(timeout_s: float, *, max_bytes: int = 5_000_000, deadline_s: float = 20.0,
                transport=None) -> Fetch:
    """Prod fetch: a hardened httpx GET marked with the auditor UA. Returns (raw bytes, status). The
    raw BYTES are what the attestation's raw_fetched_digest binds, so they are handed back verbatim.

    Bounded against a HOSTILE agent's response (httpx's per-read timeout is only the inter-chunk gap,
    which a slow-drip resets forever, and r.content buffers unboundedly):
      * STREAMS the body and aborts once it exceeds `max_bytes` — a giant response can't OOM the
        out-of-band watchdog;
      * enforces a TOTAL wall-clock `deadline_s` across the whole download — a byte-per-(timeout-ε)
        trickle can't hang the poll thread indefinitely.
    Either abort raises, which audit_incident catches and turns into an honest FAIL. `transport` is a
    test seam (httpx.MockTransport)."""
    import time

    import httpx
    client = httpx.Client(timeout=httpx.Timeout(timeout_s),
                          headers={"User-Agent": "airbag-auditor/1"}, transport=transport)

    def _fetch(url: str) -> tuple[bytes, int]:
        deadline = time.monotonic() + deadline_s
        with client.stream("GET", url) as r:
            status = r.status_code
            buf = bytearray()
            for chunk in r.iter_bytes():
                buf.extend(chunk)
                if len(buf) > max_bytes:
                    raise ValueError(f"proof body exceeded {max_bytes} bytes")
                if time.monotonic() > deadline:
                    raise TimeoutError(f"total fetch deadline {deadline_s}s exceeded")
            return bytes(buf), status

    return _fetch
