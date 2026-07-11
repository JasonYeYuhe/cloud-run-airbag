"""Counter-signed, CHAINED checkpoints over the transparency log (v6 Phase 2, B.5b).

walk() (transparency_audit) proves the WALKED RANGE is internally consistent + signed; the CHECKPOINT
proves nothing was truncated or rewritten BETWEEN audits. The auditor persists its last-attested head
(seq, entry_hash) in AUDITOR-OWNED storage. The active anti-tamper teeth are:
- CONTAINMENT: the current chain MUST still contain (re-hash the entry at) the prior checkpoint's
  (seq, entry_hash). If not -> the tail was truncated or the history rewritten -> attestation FAILs,
  and the trusted checkpoint is NOT advanced (the auditor must never "accept" a rewrite). Because each
  entry's hash pins its prev_entry_hash, the whole prefix 1..prior_seq is transitively pinned.
- ANTI-RESET: a log the auditor has attested before whose checkpoint is now MISSING is a tamper signal
  (the audited party wiping its auditor's memory), NOT a fresh start -> FAIL.
- Domain-separated (airbag.checkpoint.v1) so a checkpoint hash can never be replayed as a log-entry or
  attestation digest; the counter-signature reuses attestation's kernel (offline-verifiable).

Each checkpoint core also RECORDS its predecessor's checkpoint_hash (prev_checkpoint_hash) — a signed
LINEAGE an external re-auditor can walk offline. The auditor itself keeps only the LATEST checkpoint and
does NOT re-verify a checkpoint chain across audits; all live teeth are containment + anti-reset.

HONEST LIMITS (documented, not defended here):
- The STORE must be auditor-OWNED and `known` must be at LEAST as durable as (and independently harder
  to delete than) the checkpoint, or anti-reset adds nothing. A FULL store wipe (checkpoint AND known
  gone) re-baselines freely — inherent to any auditor-owned store (whoever can wipe it defeats any
  scheme); the defense is that the store lives in the auditor's own administrative domain.
- FORK / split-view: containment catches a fork only if the auditor's OWN view crosses branches between
  audits. A persistent split-view (auditor always served branch A, clients branch B) needs witness
  gossip — the classic transparency-log limit, out of scope.

Deterministic + LLM-free. MemoryCheckpointStore for dev+test; prod uses an auditor-OWNED bucket or the
auditor project's Firestore (chosen at deploy).
"""
from __future__ import annotations

import hashlib

import attestation          # counter_sign: canonicalize + auditor-KMS sign -> offline-verifiable envelope
import transparency_audit   # walk() + coverage() + fetch_chain() over the read seam
import verify               # _canonical (must match the agent proof canonicalization)

CHECKPOINT_TAG = "airbag.checkpoint.v1"
GENESIS_CHECKPOINT = "genesis-checkpoint"


class MemoryCheckpointStore:
    """In-process checkpoint store for dev+test. `known` (has this log EVER been checkpointed) is kept
    SEPARATE from the checkpoint itself so a test — and, with a real durable backend, the anti-reset
    defense — can distinguish 'deleted checkpoint' (reset attack) from 'never seen' (fresh start).

    Durability contract a prod backend MUST honor: `known` must be at LEAST as durable as, and
    independently harder to delete than, the checkpoint (e.g. append-only) — otherwise deleting the
    checkpoint also clears `known` and anti-reset provides zero marginal protection."""

    def __init__(self):
        self._cp: dict[str, dict] = {}
        self._known: set[str] = set()

    def get(self, log_key: str) -> dict | None:
        return self._cp.get(log_key)

    def put(self, log_key: str, checkpoint: dict) -> None:
        self._cp[log_key] = checkpoint
        self._known.add(log_key)

    def known(self, log_key: str) -> bool:
        return log_key in self._known


def checkpoint_hash(core: dict) -> str:
    """Domain-separated self-hash of a checkpoint's core (used to CHAIN one checkpoint to the next)."""
    return "sha256:" + hashlib.sha256(
        (CHECKPOINT_TAG + ":" + verify._canonical(core)).encode("utf-8")).hexdigest()


def _containment(prior: dict | None, head_seq: int, entries_by_seq: dict) -> tuple[bool, str]:
    """Is the prior checkpoint's (seq, entry_hash) still present in the CURRENT chain? RE-COMPUTES the
    entry hash at that seq (never trusts the fetched stored entry_hash) so containment is sound
    STANDALONE — an attacker forging a stored hash to match the prior can't fool it here."""
    if prior is None:
        return True, "no prior checkpoint (first attestation of this log)"
    pseq = prior.get("head_seq")
    phash = prior.get("head_entry_hash")
    if not isinstance(pseq, int) or pseq > head_seq:
        return False, f"chain truncated below prior checkpoint seq {pseq} (head is now {head_seq})"
    if pseq == 0:
        return True, "prior checkpoint was the empty log"
    e = entries_by_seq.get(pseq)
    if not isinstance(e, dict) or transparency_audit.entry_hash(e) != phash:
        return False, f"prior checkpoint (seq {pseq}) NOT contained in the current chain — rewrite/fork"
    return True, f"prior checkpoint seq {pseq} contained"


def attest_chain(log_key: str, walk_verdict: dict, entries: list[dict], *, store, signer,
                 verified_at: float) -> dict:
    """Build + counter-sign a CHAINED checkpoint after containment + anti-reset checks, and (only on a
    clean verdict) advance the trusted checkpoint. Returns the full audit result. `ok` is the overall
    verdict: the walked chain is intact AND the prior checkpoint is contained AND no reset was detected.

    The trusted checkpoint is advanced ONLY when ok — a detected rewrite/truncation/reset must never
    overwrite the last-known-good state the NEXT audit chains to."""
    prior = store.get(log_key)
    anti_reset_ok = not (store.known(log_key) and prior is None)   # known-but-missing = reset attack
    head_seq = int(walk_verdict.get("head_seq") or 0)
    # coerce seq the SAME way walk() does (transparency_audit._as_int) — a hostile entry with a
    # non-hashable seq (a list/object) must NOT crash the audit (liveness = suppression), and the
    # keying must align with walk() so containment never misses an entry walk() accepted.
    entries_by_seq: dict[int, dict] = {}
    for e in entries:
        if isinstance(e, dict):
            s = transparency_audit._as_int(e.get("seq"))
            if s is not None:
                entries_by_seq[s] = e
    contained, contain_reason = _containment(prior, head_seq, entries_by_seq)

    core = {
        "checkpoint_version": CHECKPOINT_TAG,
        "log_key": log_key,
        "head_seq": head_seq,
        "head_entry_hash": walk_verdict.get("recomputed_head_hash"),
        "prev_checkpoint_seq": prior.get("head_seq") if prior else None,
        "prev_checkpoint_hash": prior.get("checkpoint_hash") if prior else GENESIS_CHECKPOINT,
        "chain_intact": bool(walk_verdict.get("chain_intact")),
        "gaps": walk_verdict.get("gaps") or [],
        "unlogged": walk_verdict.get("unlogged") or [],
        "contained": contained,
        "anti_reset_ok": anti_reset_ok,
        "verified_at": verified_at,
    }
    core["checkpoint_hash"] = checkpoint_hash({k: v for k, v in core.items() if k != "checkpoint_hash"})
    envelope = attestation.counter_sign(core, signer=signer)   # offline-verifiable by verify.attest

    ok = bool(walk_verdict.get("chain_intact")) and contained and anti_reset_ok
    if ok:
        # advance the trusted checkpoint ONLY on a clean verdict (never overwrite good state with a rewrite)
        store.put(log_key, {"head_seq": head_seq, "head_entry_hash": core["head_entry_hash"],
                            "checkpoint_hash": core["checkpoint_hash"], "envelope": envelope})
    reason = ("verified" if ok else
              ("reset detected — prior checkpoint missing for a known log" if not anti_reset_ok else
               contain_reason if not contained else "chain not intact"))
    return {"ok": ok, "contained": contained, "anti_reset_ok": anti_reset_ok,
            "chain_intact": bool(walk_verdict.get("chain_intact")), "advanced": ok,
            "checkpoint_envelope": envelope, "reason": reason}


def audit_log(fetch, agent_url: str, *, pem_bytes: bytes | None, expected_key: str | None,
              store, signer, verified_at: float, listed_incident_ids=None, log_key: str | None = None):
    """The full Phase-2 log audit the poller runs each cycle: fetch the chain over the read seam ONCE,
    walk it (links + gaps + per-entry signer pin) + coverage cross-check, then containment + anti-reset
    + a counter-signed CHAINED checkpoint. Returns the merged verdict {chain_intact, gaps, unlogged,
    contained, anti_reset_ok, ok, checkpoint_envelope, ...}. Fail-open: an unreachable log -> ok=None,
    and the trusted checkpoint is NOT touched (absence of a fetch is not evidence of tampering)."""
    key = log_key or agent_url.rstrip("/")
    head, entries = transparency_audit.fetch_chain(fetch, agent_url)
    if head is None:
        return {"log_reachable": False, "ok": None, "chain_intact": None, "contained": None,
                "anti_reset_ok": None, "gaps": [], "unlogged": [], "checkpoint_envelope": None}
    v = transparency_audit.walk(head, entries, pem_bytes=pem_bytes, expected_key=expected_key)
    v["unlogged"] = transparency_audit.coverage(v["incident_ids"], listed_incident_ids)
    v["log_reachable"] = True
    cp = attest_chain(key, v, entries, store=store, signer=signer, verified_at=verified_at)
    return {**v, **cp}
