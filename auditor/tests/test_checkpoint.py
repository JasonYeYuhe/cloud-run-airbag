"""v6 Phase 2 (B.5b) — counter-signed, CHAINED checkpoints. walk() proves the walked range; the
checkpoint proves nothing was truncated/rewritten BETWEEN audits (containment of the prior head) and
that the auditor's own memory wasn't reset (anti-reset). Auditor-OWNED store (memory double here)."""
import base64
import hashlib

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, utils

import checkpoint as cp
import transparency_audit as ta
import verify


def _signer(priv, key="auditor/1"):
    """An attestation.Signer that mimics the auditor's KMS counter-signer (Prehashed over the digest)."""
    def sign(digest):
        raw = bytes.fromhex(digest.split(":", 1)[-1])
        der = priv.sign(raw, ec.ECDSA(utils.Prehashed(hashes.SHA256())))
        return {"algorithm": "EC_SIGN_P256_SHA256", "key": key, "signature": base64.b64encode(der).decode()}
    return sign


def _chain(n, evil_at=None):
    """A deterministic n-link chain (seq 1..k are byte-identical across calls). evil_at rewrites one
    incident_id so that seq's entry_hash (and everything after) diverges."""
    entries, prev = [], ta.GENESIS
    for s in range(1, n + 1):
        iid = "inc-EVIL" if s == evil_at else f"inc-{s}"
        core = {"seq": s, "prev_entry_hash": prev, "incident_id": iid, "service": "svc",
                "terminal_status": "mitigated", "bundle_digest": "sha256:" + f"{s:064x}",
                "signature": {"key": "kms/1", "signature": "s"}, "ts": 100.0 + s}
        eh = ta.entry_hash(core)
        entries.append({**core, "entry_hash": eh})
        prev = eh
    return ta.walk({"seq": n, "prev_entry_hash": prev}, entries), entries


def test_first_checkpoint_establishes_a_signed_baseline():
    priv, store = ec.generate_private_key(ec.SECP256R1()), cp.MemoryCheckpointStore()
    verdict, entries = _chain(3)
    r = cp.attest_chain("agent-log", verdict, entries, store=store, signer=_signer(priv), verified_at=1.0)
    assert r["ok"] and r["contained"] and r["anti_reset_ok"] and r["advanced"]
    assert store.get("agent-log")["head_seq"] == 3 and store.known("agent-log")


def test_second_checkpoint_chains_to_and_advances_past_the_first():
    priv, store = ec.generate_private_key(ec.SECP256R1()), cp.MemoryCheckpointStore()
    v1, e1 = _chain(2)
    cp.attest_chain("log", v1, e1, store=store, signer=_signer(priv), verified_at=1.0)
    first = store.get("log")
    v2, e2 = _chain(4)                                     # the chain grew (still contains seq 1..2)
    r = cp.attest_chain("log", v2, e2, store=store, signer=_signer(priv), verified_at=2.0)
    assert r["ok"] and r["contained"]
    assert r["checkpoint_envelope"]["bundle"]["prev_checkpoint_hash"] == first["checkpoint_hash"]
    assert store.get("log")["head_seq"] == 4              # advanced


def test_truncation_below_prior_fails_and_keeps_the_good_checkpoint():
    priv, store = ec.generate_private_key(ec.SECP256R1()), cp.MemoryCheckpointStore()
    v5, e5 = _chain(5)
    cp.attest_chain("log", v5, e5, store=store, signer=_signer(priv), verified_at=1.0)
    good = store.get("log")                               # checkpoint at seq 5
    v3, e3 = _chain(3)                                    # the chain was TRUNCATED to seq 3
    r = cp.attest_chain("log", v3, e3, store=store, signer=_signer(priv), verified_at=2.0)
    assert not r["ok"] and not r["contained"] and not r["advanced"]
    assert store.get("log") == good                       # the trusted checkpoint is UNCHANGED


def test_rewrite_at_the_checkpoint_seq_is_not_contained():
    priv, store = ec.generate_private_key(ec.SECP256R1()), cp.MemoryCheckpointStore()
    v3, e3 = _chain(3)
    cp.attest_chain("log", v3, e3, store=store, signer=_signer(priv), verified_at=1.0)
    v_evil, e_evil = _chain(3, evil_at=2)                 # a re-linked chain with seq 2 rewritten
    assert v_evil["chain_intact"]                         # internally consistent (attacker re-hashed)
    r = cp.attest_chain("log", v_evil, e_evil, store=store, signer=_signer(priv), verified_at=2.0)
    assert not r["ok"] and not r["contained"]             # prior checkpoint's (seq 3, hash) is gone


def test_anti_reset_known_log_with_missing_checkpoint_fails():
    priv, store = ec.generate_private_key(ec.SECP256R1()), cp.MemoryCheckpointStore()
    v3, e3 = _chain(3)
    cp.attest_chain("log", v3, e3, store=store, signer=_signer(priv), verified_at=1.0)
    store._cp.pop("log")                                  # attacker deletes the checkpoint; KNOWN survives
    assert store.get("log") is None and store.known("log")
    r = cp.attest_chain("log", v3, e3, store=store, signer=_signer(priv), verified_at=2.0)
    assert not r["ok"] and not r["anti_reset_ok"]


def test_a_broken_chain_never_establishes_a_baseline():
    priv, store = ec.generate_private_key(ec.SECP256R1()), cp.MemoryCheckpointStore()
    verdict, entries = _chain(3)
    entries[1]["bundle_digest"] = "sha256:" + "ff" * 32   # tamper -> broken link -> not intact
    verdict = ta.walk({"seq": 3, "prev_entry_hash": entries[2]["entry_hash"]}, entries)
    assert not verdict["chain_intact"]
    r = cp.attest_chain("log", verdict, entries, store=store, signer=_signer(priv), verified_at=1.0)
    assert not r["ok"] and not r["advanced"] and store.get("log") is None


def test_checkpoint_envelope_is_offline_verifiable_by_the_kernel():
    priv, store = ec.generate_private_key(ec.SECP256R1()), cp.MemoryCheckpointStore()
    pem = priv.public_key().public_bytes(serialization.Encoding.PEM,
                                         serialization.PublicFormat.SubjectPublicKeyInfo)
    v, e = _chain(2)
    env = cp.attest_chain("log", v, e, store=store, signer=_signer(priv), verified_at=1.0)["checkpoint_envelope"]
    verdict = verify.attest(env, expected_pem=pem, expected_key="auditor/1")
    assert verdict["tri_state"] == verify.SIGNED_VERIFIED           # a checkpoint verifies like an attestation
    assert env["bundle"]["checkpoint_version"] == "airbag.checkpoint.v1"


def test_checkpoint_hash_is_domain_separated():
    core = {"head_seq": 1, "head_entry_hash": "sha256:x"}
    tagged = cp.checkpoint_hash(core)
    plain = "sha256:" + hashlib.sha256(verify._canonical(core).encode()).hexdigest()
    assert tagged != plain
    assert tagged == "sha256:" + hashlib.sha256(
        (cp.CHECKPOINT_TAG + ":" + verify._canonical(core)).encode()).hexdigest()


def test_audit_log_end_to_end_over_a_fake_seam():
    import json
    priv, store = ec.generate_private_key(ec.SECP256R1()), cp.MemoryCheckpointStore()
    _, entries = _chain(3)
    head = {"seq": 3, "prev_entry_hash": entries[2]["entry_hash"]}

    def _fetch(url):
        if url.endswith("/transparency/head"):
            return json.dumps(head).encode(), 200
        if "/transparency/log" in url:
            return json.dumps({"entries": entries}).encode(), 200
        return b"", 404
    r = cp.audit_log(_fetch, "http://agent", pem_bytes=None, expected_key=None, store=store,
                     signer=_signer(priv), verified_at=1.0, listed_incident_ids=["inc-1", "inc-9"])
    assert r["ok"] and r["chain_intact"] and r["contained"]
    assert r["unlogged"] == ["inc-9"]                     # listed by /incidents but never chained
    assert store.get("http://agent")["head_seq"] == 3     # checkpoint advanced under the agent-url key


def test_hostile_unhashable_seq_never_crashes_the_audit():
    """A compromised agent serving one entry with a non-hashable seq (a list/object) must NOT crash the
    audit cycle (liveness = suppression) — attest_chain coerces seq the same way walk() does."""
    priv, store = ec.generate_private_key(ec.SECP256R1()), cp.MemoryCheckpointStore()
    verdict, entries = _chain(2)
    entries.append({"seq": [99], "entry_hash": "x"})      # unhashable seq -> would crash a raw dict-key
    verdict = ta.walk({"seq": 2, "prev_entry_hash": entries[1]["entry_hash"]}, entries)
    r = cp.attest_chain("log", verdict, entries, store=store, signer=_signer(priv), verified_at=1.0)
    assert r["ok"]                                         # the 2 real links verify; the junk entry is ignored


def test_audit_log_fail_open_when_unreachable():
    store = cp.MemoryCheckpointStore()
    r = cp.audit_log(lambda url: (b"", 503), "http://agent", pem_bytes=None, expected_key=None,
                     store=store, signer=lambda d: None, verified_at=1.0)
    assert r["log_reachable"] is False and r["ok"] is None
    assert store.get("http://agent") is None              # an unreachable log never touches the checkpoint
