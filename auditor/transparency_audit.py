"""Walk the agent's hash-chained transparency log and prove no LOGGED incident was deleted, reordered,
or back-dated (v6 Phase 2). The auditor is HTTPS-only: it fetches GET /transparency/head + /log over
the same outbound path as the proofs, and RE-IMPLEMENTS the chain math + per-entry crypto (NEVER
trusting the agent's stored entry_hash) — the same independence property as the verify kernel.

Deterministic + LLM-free; stdlib + the sibling verify kernel + cryptography. Guarded by the auditor's
denylist half of test_auditor_invariant.py (no agent / no LLM imports). The pure functions (walk,
coverage) are testable directly on data; fetch_chain does the outbound HTTP via the Fetch callable.
"""
from __future__ import annotations

import base64
import hashlib
import json
from typing import Callable

import verify   # sibling auditor kernel (its _canonical must match the agent's proof canonicalization)

# MUST match agent autosre.transparency.* EXACTLY — re-implemented here, never imported (independence).
ENTRY_TAG = "airbag.log.entry.v1"
GENESIS = "genesis"
CORE_FIELDS = ("seq", "prev_entry_hash", "incident_id", "service", "terminal_status",
               "bundle_digest", "signature", "ts")

# (bytes, http_status) — the same fetch contract poller.Fetch uses.
Fetch = Callable[[str], "tuple[bytes, int]"]


def entry_hash(entry: dict) -> str:
    """Recompute an entry's domain-separated chain hash from its core fields (NOT the stored value)."""
    core = {k: entry.get(k) for k in CORE_FIELDS}
    return "sha256:" + hashlib.sha256((ENTRY_TAG + ":" + verify._canonical(core)).encode("utf-8")).hexdigest()


def verify_entry_signature(entry: dict, pem_bytes: bytes | None, expected_key: str | None) -> bool | None:
    """Verify the entry's KMS signature over its bundle_digest — Prehashed(SHA256), because the log
    stores the DIGEST, not the bundle bytes — against the PINNED key. Returns True (verified + signer
    pinned) / False (bad sig OR wrong signer) / None (unsigned or no key supplied). Fully defensive
    against hostile fetched JSON (never raises)."""
    sig = entry.get("signature")
    if not isinstance(sig, dict) or not sig.get("signature") or not pem_bytes:
        return None
    try:
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ec, utils
        raw = bytes.fromhex((entry.get("bundle_digest") or "").split(":", 1)[-1])
        pub = serialization.load_pem_public_key(pem_bytes)
        pub.verify(base64.b64decode(sig["signature"]), raw, ec.ECDSA(utils.Prehashed(hashes.SHA256())))
    except Exception:  # noqa: BLE001 — a bad/garbled signature FAILs, it never crashes the audit
        return False
    if expected_key is not None and sig.get("key") != expected_key:
        return False                      # valid signature but UNEXPECTED signer -> FAIL (the pin)
    return True


def _as_int(v) -> int | None:
    """Coerce a fetched seq to int, or None on garbage (a hostile agent must not crash the audit)."""
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def walk(head: dict, entries: list[dict], *, pem_bytes: bytes | None = None,
         expected_key: str | None = None) -> dict:
    """Recompute every link over the fetched head + entries and return the audit verdict:
      {chain_intact, gaps, broken_links, signature_failures, orphan_entries, malformed_head, head_seq,
       recomputed_head_hash, incident_ids}
    - gaps: an expected seq (1..head_seq) missing from the fetched entries (a deletion signal).
    - broken_links: an entry whose recomputed entry_hash != its stored value, OR whose prev_entry_hash
      != the previous present entry's recomputed hash (tamper / reorder).
    - signature_failures: an entry whose signature is invalid, whose signer isn't the pinned key, OR
      which is UNSIGNED while a key is pinned (a signature-STRIP rewrite — a legitimate chain never
      contains an unsigned entry, since append() fires only for a SIGNED heal; mirrors verify.py DEGRADED).
    - orphan_entries / malformed_head: entries beyond the claimed head, or an unparseable head.seq.
    - chain_intact: NONE of the above AND the head's prev_entry_hash == the last entry's recomputed hash.

    HONEST LIMIT (closed only by the Phase-2b counter-signed CHECKPOINT): walk() iterates 1..head_seq
    and has no independent record of the true head, so a truncation-to-matching-head (delete the tail
    AND lower head.seq/prev to match) still reads intact. The auditor's persisted last-attested
    (seq, entry_hash) checkpoint is what detects that across audits; chain_intact means "the walked
    range is internally consistent + signed", not "nothing was ever truncated".
    """
    if not isinstance(head, dict):
        head = {}
    head_seq = _as_int(head.get("seq"))
    malformed_head = head_seq is None or head_seq < 0
    if malformed_head:
        head_seq = 0
    by_seq: dict[int, dict] = {}
    for e in entries:
        if isinstance(e, dict):
            s = _as_int(e.get("seq"))
            if s is not None and s >= 1:
                by_seq[s] = e             # last wins on a duplicate seq (a swap still needs valid links)
    gaps: list[int] = []
    broken: list[int] = []
    sig_failures: list[int] = []
    incident_ids: list[str] = []
    prev_hash = GENESIS
    last_hash = GENESIS
    for s in range(1, head_seq + 1):
        e = by_seq.get(s)
        if e is None:
            gaps.append(s)                # missing seq: don't advance prev_hash (the next link breaks too)
            continue
        recomputed = entry_hash(e)
        if recomputed != e.get("entry_hash") or e.get("prev_entry_hash") != prev_hash:
            broken.append(s)
        sig_verdict = verify_entry_signature(e, pem_bytes, expected_key)
        if sig_verdict is False or (pem_bytes is not None and sig_verdict is None):
            sig_failures.append(s)        # bad sig, wrong signer, OR unsigned-while-a-key-is-pinned
        iid = e.get("incident_id")
        if iid:
            incident_ids.append(iid)
        prev_hash = recomputed
        last_hash = recomputed
    orphans = sorted(s for s in by_seq if s > head_seq)   # entries BEYOND the claimed head (inconsistent)
    if head_seq == 0:
        head_ok = not by_seq              # an empty head is intact ONLY if no entries actually exist
    else:
        head_ok = (not gaps) and head.get("prev_entry_hash") == last_hash
    chain_intact = (not malformed_head and not gaps and not broken and not sig_failures
                    and not orphans and head_ok)
    return {"chain_intact": chain_intact, "gaps": gaps, "broken_links": broken,
            "signature_failures": sig_failures, "orphan_entries": orphans,
            "malformed_head": malformed_head, "head_seq": head_seq,
            "recomputed_head_hash": last_hash, "incident_ids": sorted(set(incident_ids))}


def coverage(chain_incident_ids, listed_incident_ids) -> list[str]:
    """unlogged: incidents the agent LISTS (GET /incidents) that never entered the chain — HONEST, since
    the fail-open append + the PROOF_SIGN early-return mean an incident can legitimately never log. This
    cross-check names what the chain does NOT cover (narrower than a no-suppression proof, and says so)."""
    logged = set(chain_incident_ids)
    return sorted(iid for iid in dict.fromkeys(listed_incident_ids or []) if iid and iid not in logged)


def _get_json(fetch: Fetch, url: str, max_bytes: int = 5_000_000):
    """Fetch + parse JSON, fail-open to None on any error/oversize/non-200 (hostile agent must not crash
    the out-of-band audit — defeating the auditor's LIVENESS is itself the suppression attack)."""
    try:
        raw, status = fetch(url)
        if status != 200 or raw is None or len(raw) > max_bytes:
            return None
        return json.loads(raw)
    except Exception:  # noqa: BLE001
        return None


def fetch_chain(fetch: Fetch, agent_url: str, *, page: int = 1000, max_entries: int = 100_000):
    """Fetch the head + ALL entries (paginated) over the read seam. Returns (head, entries) or (None, [])
    on failure. Bounded by max_entries so a hostile head.seq can't drive an unbounded walk."""
    base = agent_url.rstrip("/")
    head = _get_json(fetch, f"{base}/transparency/head")
    if not isinstance(head, dict):
        return None, []
    head_seq = min(int(head.get("seq", 0) or 0), max_entries)
    entries: list[dict] = []
    lo = 1
    while lo <= head_seq:
        hi = min(lo + page - 1, head_seq)
        body = _get_json(fetch, f"{base}/transparency/log?from={lo}&to={hi}")
        batch = body.get("entries") if isinstance(body, dict) else None
        if not batch:
            break                          # a gap/short page surfaces to walk() as a missing seq
        entries.extend(e for e in batch if isinstance(e, dict))
        lo = hi + 1
    return head, entries


def audit_chain(fetch: Fetch, agent_url: str, *, pem_bytes: bytes | None, expected_key: str | None,
                listed_incident_ids=None) -> dict:
    """Full out-of-band chain audit: fetch head+entries -> walk (links + gaps + per-entry signer pin) ->
    coverage cross-check. Returns the walk verdict PLUS `unlogged`. Fail-open: an unreachable log yields
    chain_intact for an empty head (nothing to suppress) but records the fetch failure honestly."""
    head, entries = fetch_chain(fetch, agent_url)
    if head is None:
        return {"chain_intact": None, "gaps": [], "broken_links": [], "signature_failures": [],
                "head_seq": None, "recomputed_head_hash": None, "incident_ids": [], "unlogged": [],
                "log_reachable": False}
    v = walk(head, entries, pem_bytes=pem_bytes, expected_key=expected_key)
    v["unlogged"] = coverage(v["incident_ids"], listed_incident_ids)
    v["log_reachable"] = True
    return v
