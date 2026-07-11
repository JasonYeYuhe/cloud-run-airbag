"""v6 Phase 2 — the auditor walks the agent's hash-chained transparency log and INDEPENDENTLY proves no
LOGGED incident was deleted, reordered, or back-dated. Pure functions tested on synthetic chains (built
with the SAME entry_hash formula the auditor recomputes) + a fake Fetch seam."""
import base64
import json

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, utils

import transparency_audit as ta


def _sign_digest(priv, bundle_digest):
    raw = bytes.fromhex(bundle_digest.split(":", 1)[-1])
    der = priv.sign(raw, ec.ECDSA(utils.Prehashed(hashes.SHA256())))
    return {"algorithm": "EC_SIGN_P256_SHA256", "key": "kms/1", "signature": base64.b64encode(der).decode()}


def _chain(n, priv=None, key="kms/1"):
    """A valid n-link chain built with the SAME entry_hash formula the auditor recomputes."""
    entries, prev = [], ta.GENESIS
    for s in range(1, n + 1):
        digest = "sha256:" + f"{s:064x}"
        core = {"seq": s, "prev_entry_hash": prev, "incident_id": f"inc-{s}", "service": "svc",
                "terminal_status": "mitigated", "bundle_digest": digest,
                "signature": _sign_digest(priv, digest) if priv else {"key": key, "signature": "s"},
                "ts": 100.0 + s}
        eh = ta.entry_hash(core)
        entries.append({**core, "entry_hash": eh})
        prev = eh
    return {"seq": n, "prev_entry_hash": prev}, entries


def test_intact_chain_verifies():
    head, entries = _chain(3)
    v = ta.walk(head, entries)
    assert v["chain_intact"] and v["gaps"] == [] and v["broken_links"] == []
    assert v["incident_ids"] == ["inc-1", "inc-2", "inc-3"]


def test_empty_log_is_intact():
    assert ta.walk({"seq": 0, "prev_entry_hash": ta.GENESIS}, [])["chain_intact"] is True


def test_deleted_entry_is_a_gap():
    head, entries = _chain(3)
    del entries[1]                                        # remove seq 2
    v = ta.walk(head, entries)
    assert not v["chain_intact"] and v["gaps"] == [2]


def test_tampered_field_breaks_the_recomputed_hash():
    head, entries = _chain(3)
    entries[1]["bundle_digest"] = "sha256:" + "ff" * 32   # tamper -> recomputed entry_hash != stored
    v = ta.walk(head, entries)
    assert not v["chain_intact"] and 2 in v["broken_links"]


def test_broken_prev_hash_is_detected():
    head, entries = _chain(3)
    entries[1]["prev_entry_hash"] = "sha256:" + "00" * 32   # entry 2's link no longer points at entry 1
    v = ta.walk(head, entries)
    assert not v["chain_intact"] and 2 in v["broken_links"]


def test_backdated_head_detected():
    head, entries = _chain(3)
    head["prev_entry_hash"] = "sha256:" + "00" * 32        # head doesn't point at the last entry
    v = ta.walk(head, entries)
    assert not v["chain_intact"] and v["gaps"] == [] and v["broken_links"] == []


def test_per_entry_signature_pin():
    priv = ec.generate_private_key(ec.SECP256R1())
    pem = priv.public_key().public_bytes(serialization.Encoding.PEM,
                                         serialization.PublicFormat.SubjectPublicKeyInfo)
    head, entries = _chain(2, priv=priv, key="kms/1")
    v = ta.walk(head, entries, pem_bytes=pem, expected_key="kms/1")
    assert v["chain_intact"] and v["signature_failures"] == []
    # a valid signature but the WRONG pinned key -> the pin FAILs every entry
    v2 = ta.walk(head, entries, pem_bytes=pem, expected_key="kms/2")
    assert not v2["chain_intact"] and v2["signature_failures"] == [1, 2]
    # a DIFFERENT key's PEM -> the crypto itself fails
    other = ec.generate_private_key(ec.SECP256R1())
    other_pem = other.public_key().public_bytes(serialization.Encoding.PEM,
                                                serialization.PublicFormat.SubjectPublicKeyInfo)
    assert ta.walk(head, entries, pem_bytes=other_pem, expected_key="kms/1")["signature_failures"] == [1, 2]


def _stripped_chain(n):
    """A chain an attacker forged after STRIPPING signatures: links are self-consistent (the hash is
    public), but no entry carries a signature."""
    entries, prev = [], ta.GENESIS
    for s in range(1, n + 1):
        core = {"seq": s, "prev_entry_hash": prev, "incident_id": f"inc-{s}", "service": "svc",
                "terminal_status": "mitigated", "bundle_digest": "sha256:" + f"{s:064x}",
                "signature": None, "ts": 100.0 + s}
        eh = ta.entry_hash(core)
        entries.append({**core, "entry_hash": eh})
        prev = eh
    return {"seq": n, "prev_entry_hash": prev}, entries


def test_signature_strip_is_detected_when_a_key_is_pinned():
    """The strip attack (the key threat — a compromised agent): remove every signature, re-link the
    public hash chain. Links stay self-consistent, but with a key PINNED an unsigned entry IS tamper."""
    priv = ec.generate_private_key(ec.SECP256R1())
    pem = priv.public_key().public_bytes(serialization.Encoding.PEM,
                                         serialization.PublicFormat.SubjectPublicKeyInfo)
    head, entries = _stripped_chain(3)
    assert ta.walk(head, entries)["broken_links"] == []               # the hash chain IS self-consistent
    v = ta.walk(head, entries, pem_bytes=pem, expected_key="kms/1")
    assert not v["chain_intact"] and v["signature_failures"] == [1, 2, 3]


def test_unsigned_chain_without_a_pinned_key_is_integrity_only():
    """With NO key supplied the auditor can't check signatures, so an unsigned chain is integrity-only
    (links verify) and must NOT be spuriously failed."""
    head, entries = _stripped_chain(2)
    v = ta.walk(head, entries)                                          # no pem_bytes
    assert v["chain_intact"] and v["signature_failures"] == []


def test_head_seq_zero_with_entries_present_is_not_intact():
    """The degenerate truncation-to-zero: claim head.seq=0 while entries still exist -> NOT intact."""
    _, entries = _chain(2)
    v = ta.walk({"seq": 0, "prev_entry_hash": ta.GENESIS}, entries)
    assert not v["chain_intact"] and v["orphan_entries"] == [1, 2]


def test_malformed_seq_never_crashes_the_audit():
    head, entries = _chain(2)
    entries += [{"seq": None, "entry_hash": "x"}, {"seq": "abc"}]       # hostile entries with bad seq
    v = ta.walk(head, entries)                                          # must NOT raise
    assert v["chain_intact"] and v["head_seq"] == 2                    # the 2 real links verify; junk ignored
    assert ta.walk({"seq": "abc", "prev_entry_hash": "x"}, entries)["malformed_head"] is True
    assert ta.walk({"seq": "abc"}, [])["chain_intact"] is False


def test_coverage_names_unlogged_incidents():
    assert ta.coverage(["inc-1", "inc-3"], ["inc-1", "inc-2", "inc-3", "inc-4"]) == ["inc-2", "inc-4"]


def test_fetch_chain_and_audit_over_a_fake_seam():
    head, entries = _chain(2)

    def _fetch(url):
        if url.endswith("/transparency/head"):
            return json.dumps(head).encode(), 200
        if "/transparency/log" in url:
            return json.dumps({"entries": entries}).encode(), 200
        return b"", 404
    v = ta.audit_chain(_fetch, "http://agent", pem_bytes=None, expected_key=None,
                       listed_incident_ids=["inc-1", "inc-2", "inc-9"])
    assert v["log_reachable"] and v["chain_intact"]
    assert v["unlogged"] == ["inc-9"]                    # listed by /incidents but never entered the chain


def test_audit_fail_open_when_the_log_is_unreachable():
    v = ta.audit_chain(lambda url: (b"", 503), "http://agent", pem_bytes=None, expected_key=None)
    assert v["log_reachable"] is False and v["chain_intact"] is None


def test_walk_is_defensive_against_hostile_entries():
    # a non-dict entry / missing fields must never crash the walk (a compromised agent's suppression path)
    head = {"seq": 2, "prev_entry_hash": "sha256:x"}
    v = ta.walk(head, ["not-a-dict", {"seq": 1}])
    assert v["chain_intact"] is False                    # honest FAIL, no exception
