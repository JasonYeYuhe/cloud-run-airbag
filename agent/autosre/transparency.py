"""Hash-chained transparency log (v6 Phase 2) — the tamper-evident SPINE the auditor walks.

Each committed, SIGNED heal appends an immutable, hash-chained entry so an INDEPENDENT auditor can
prove no LOGGED incident was deleted, reordered, or back-dated since its first counter-signed
checkpoint. Deterministic + LLM-free (joins autosre/_action_files()).

- append() commits the head pointer + the immutable `log_entries/{seq}` doc in ONE `transact_multi`
  (atomic — a container kill can't forge a seq gap the auditor would read as suppression).
- entry_hash = sha256(DOMAIN-TAG || canonical({seq, prev_entry_hash, incident_id, service,
  terminal_status, bundle_digest, signature, ts})) — domain-separated (`airbag.log.entry.v1`) so an
  entry digest can never be cross-replayed as a checkpoint/attestation digest (nono's technique). The
  terminal_status is IN the hashed core so each link is self-describing (mitigated vs closed).
- Idempotency key (incident_id, terminal_status): MITIGATED + CLOSED are TWO distinct links, so keying
  on incident_id ALONE would KEEP-drop the CLOSED link. The last committed keys live ON the head doc as
  a FLAT list of scalar json.dumps strings (Firestore rejects a directly-nested array) for a pure
  in-transaction KEEP check (Cloud Tasks at-least-once redelivery must not dup a seq).
- Does NOT promise adjacency: one GLOBAL head, so another service's heal can legitimately take the seq
  between an incident's mitigated and its hours-later closed link.
- Entries store bundle_digest + signature (NOT the bundle bytes), so the auditor's per-entry provenance
  check uses the Prehashed(SHA256) verify variant against its pinned key.
- Fail-open: append() NEVER raises out — a log hiccup must never block a completed heal.
"""
from __future__ import annotations

import hashlib
import json
import logging
import time

from . import state_store

log = logging.getLogger("airbag.transparency")

HEAD_COLLECTION = "log_head"      # collection holding the single head doc
HEAD_ID = "head"                  # the one head doc id
ENTRIES_COLLECTION = "log_entries"  # collection of immutable per-seq entries
ENTRY_TAG = "airbag.log.entry.v1"   # domain-separation tag prefixed before hashing an entry
GENESIS = "genesis"               # the prev_entry_hash of the first entry
RECENT_CAP = 200                  # (incident, status) pairs remembered on the head for the KEEP check


def _canon(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def entry_hash(core: dict) -> str:
    """Domain-separated chain hash of an entry's core fields: sha256(TAG ':' canonical(core))."""
    return "sha256:" + hashlib.sha256((ENTRY_TAG + ":" + _canon(core)).encode("utf-8")).hexdigest()


def append(*, incident_id: str, service, bundle_digest: str, signature,
           terminal_status: str, ts: float | None = None) -> dict | None:
    """Append a hash-chained entry for a committed SIGNED heal. Returns the new entry, or None on an
    idempotent KEEP (this (incident_id, terminal_status) already logged) OR on any failure (fail-open).
    All hashing/head-advance happens INSIDE the transact_multi mutator so a Firestore retry re-bases on
    the fresh head (never a captured stale one)."""
    # SCALAR string key -> recent_pairs is a FLAT list of strings. Firestore rejects a directly-nested
    # array (an array element that is itself an array), so [incident_id, terminal_status] as a raw pair
    # would make EVERY append fail-open on the firestore backend. json.dumps is unambiguous regardless
    # of what characters an incident_id contains.
    key = json.dumps([incident_id, terminal_status])
    at = time.time() if ts is None else ts

    def _mutator(head):
        head = head or {"seq": 0, "prev_entry_hash": GENESIS, "recent_pairs": []}
        recent = list(head.get("recent_pairs") or [])
        if key in recent:
            return ([], None)                             # idempotent: this pair is already in the chain
        seq = int(head.get("seq", 0)) + 1
        core = {"seq": seq, "prev_entry_hash": head.get("prev_entry_hash", GENESIS),
                "incident_id": incident_id, "service": service, "terminal_status": terminal_status,
                "bundle_digest": bundle_digest, "signature": signature, "ts": at}
        eh = entry_hash(core)
        entry = {**core, "entry_hash": eh}
        new_head = {"seq": seq, "prev_entry_hash": eh,
                    "recent_pairs": (recent + [key])[-RECENT_CAP:], "updated_at": at}
        return ([(HEAD_COLLECTION, HEAD_ID, new_head),
                 (ENTRIES_COLLECTION, str(seq), entry)], entry)

    try:
        return state_store.transact_multi(HEAD_COLLECTION, HEAD_ID, _mutator)
    except Exception as e:  # noqa: BLE001 — FAIL-OPEN: a log failure must never block a completed heal
        log.warning("transparency append failed for %s/%s: %s", incident_id, terminal_status, e)
        return None


def head() -> dict | None:
    """The current head pointer ({seq, prev_entry_hash, recent_pairs, updated_at}), or None if empty."""
    return state_store.get(HEAD_COLLECTION, HEAD_ID)


def entries(from_seq: int = 1, to_seq: int | None = None) -> list[dict]:
    """The immutable entries seq in [from_seq, to_seq] (inclusive), ascending. to_seq=None -> the head.
    Read via per-seq gets (each entry doc id IS its seq) so a missing seq surfaces as a gap, not a
    silently-omitted row (the auditor treats a gap as a tamper signal)."""
    h = head()
    if not h:
        return []
    hi = int(h.get("seq", 0)) if to_seq is None else min(int(to_seq), int(h.get("seq", 0)))
    lo = max(1, int(from_seq))
    out: list[dict] = []
    for s in range(lo, hi + 1):
        doc = state_store.get(ENTRIES_COLLECTION, str(s))
        if doc is not None:
            out.append(doc)
    return out
