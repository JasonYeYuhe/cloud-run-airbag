"""v6 Phase 2 — the hash-chained transparency log. append() links each SIGNED heal into an immutable,
tamper-evident chain (via transact_multi) so an independent auditor can prove no LOGGED incident was
deleted, reordered, or back-dated. Runs against the memory backend (conftest resets the store)."""
import hashlib
import json

from autosre import state_store, transparency


def _append(inc, status="mitigated", service="svc", digest="sha256:" + "ab" * 32, sig=None):
    return transparency.append(incident_id=inc, service=service, bundle_digest=digest,
                               signature=sig or {"key": "k", "signature": "s"},
                               terminal_status=status, ts=100.0)


def test_first_append_is_the_genesis_link():
    e = _append("inc-1")
    assert e["seq"] == 1 and e["prev_entry_hash"] == transparency.GENESIS
    assert e["entry_hash"].startswith("sha256:")
    assert state_store.get("log_entries", "1") == e                     # immutable entry persisted
    h = transparency.head()
    assert h["seq"] == 1 and h["prev_entry_hash"] == e["entry_hash"]    # head advanced to the new link


def test_entries_chain_prev_to_entry_hash():
    e1 = _append("inc-1")
    e2 = _append("inc-2")
    assert e2["seq"] == 2
    assert e2["prev_entry_hash"] == e1["entry_hash"]                    # link continuity (the chain)
    assert e1["prev_entry_hash"] == transparency.GENESIS


def test_idempotent_per_incident_status_pair():
    assert _append("inc-1", "mitigated")["seq"] == 1
    dup = _append("inc-1", "mitigated")                                 # same pair -> KEEP, no dup seq
    assert dup is None
    assert transparency.head()["seq"] == 1                             # seq did NOT advance


def test_recent_pairs_is_a_flat_list_of_strings_firestore_safe():
    """Firestore rejects a directly-nested array (an array whose element is an array), so the head's
    idempotency-key list MUST be flat scalars, or every append would fail-open on the firestore backend
    (the memory backend stores nested lists fine, masking it). Guards that regression on ANY backend."""
    _append("inc-1", "mitigated")
    _append("inc-1", "closed")
    recent = transparency.head()["recent_pairs"]
    assert recent and all(isinstance(k, str) for k in recent)          # NO nested arrays


def test_mitigated_and_closed_are_two_links_for_one_incident():
    m = _append("inc-1", "mitigated")
    c = _append("inc-1", "closed")                                     # a DIFFERENT pair -> a second link
    assert m["seq"] == 1 and c["seq"] == 2
    assert c["prev_entry_hash"] == m["entry_hash"]


def test_links_for_one_incident_may_be_non_adjacent():
    m = _append("inc-1", "mitigated")             # seq 1
    _append("inc-2", "mitigated")                 # seq 2 — a FOREIGN service's heal interleaves
    c = _append("inc-1", "closed")                # seq 3 — inc-1's closed link is NON-adjacent to its mitigated
    assert m["seq"] == 1 and c["seq"] == 3
    both = [e for e in transparency.entries() if e["incident_id"] == "inc-1"]
    assert [e["seq"] for e in both] == [1, 3]                          # two valid non-adjacent links


def test_entry_stores_digest_and_signature_not_the_bundle():
    e = _append("inc-1", digest="sha256:" + "cd" * 32, sig={"key": "kms/1", "signature": "DERb64"})
    assert e["bundle_digest"] == "sha256:" + "cd" * 32
    assert e["signature"] == {"key": "kms/1", "signature": "DERb64"}
    assert "bundle" not in e                                           # never the bundle bytes


def test_entry_hash_is_domain_separated():
    core = {"seq": 1, "prev_entry_hash": "genesis", "incident_id": "i", "service": "s",
            "bundle_digest": "sha256:x", "signature": {"k": 1}, "ts": 100.0}
    tagged = transparency.entry_hash(core)
    canonical = json.dumps(core, sort_keys=True, separators=(",", ":"), default=str)
    plain = "sha256:" + hashlib.sha256(canonical.encode()).hexdigest()          # WITHOUT the domain tag
    assert tagged != plain                                            # the tag changes the digest
    assert tagged == "sha256:" + hashlib.sha256(
        (transparency.ENTRY_TAG + ":" + canonical).encode()).hexdigest()


def test_append_is_fail_open(monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("firestore down")
    monkeypatch.setattr(state_store, "transact_multi", _boom)
    assert _append("inc-1") is None                                   # never raises; a log hiccup is silent


def test_entries_read_helpers_and_range():
    for i in range(1, 6):
        _append(f"inc-{i}")
    assert [e["seq"] for e in transparency.entries()] == [1, 2, 3, 4, 5]
    assert [e["seq"] for e in transparency.entries(2, 4)] == [2, 3, 4]
    assert transparency.entries(10) == []                             # past the head -> empty
    assert transparency.head()["seq"] == 5


def test_entries_is_empty_before_any_append():
    assert transparency.head() is None
    assert transparency.entries() == []


def test_entry_hash_matches_the_auditor_reimplementation():
    """The auditor RE-IMPLEMENTS entry_hash (independence: it imports NO agent code). Pin the two
    formulas byte-identical so the auditor is a genuine independent re-run, not a divergent second
    opinion — the same parity discipline as the verify.py kernel (read by path, no import coupling)."""
    import pathlib
    import sys
    auditor_dir = str(pathlib.Path(__file__).resolve().parents[2] / "auditor")
    sys.path.insert(0, auditor_dir)
    try:
        import transparency_audit as ta
        core = {"seq": 3, "prev_entry_hash": "sha256:abc", "incident_id": "inc-1", "service": "svc",
                "terminal_status": "closed", "bundle_digest": "sha256:" + "ab" * 32,
                "signature": {"algorithm": "EC_SIGN_P256_SHA256", "key": "kms/1", "signature": "DERb64"},
                "ts": 1783736679.637}
        assert transparency.entry_hash(core) == ta.entry_hash(core)   # byte-identical chain math
        assert transparency.ENTRY_TAG == ta.ENTRY_TAG                 # and the same domain-sep tag
    finally:
        sys.path.remove(auditor_dir)
        for m in ("transparency_audit", "verify"):
            sys.modules.pop(m, None)


def test_read_only_routes_serve_the_chain_for_the_auditor():
    """The HTTPS-only auditor walks the chain via GET /transparency/head + /transparency/log."""
    from fastapi.testclient import TestClient
    import app as appmod
    c = TestClient(appmod.app)
    # empty log: head reports seq 0 (never 404), log is empty
    assert c.get("/transparency/head").json() == {"seq": 0, "prev_entry_hash": transparency.GENESIS, "empty": True}
    assert c.get("/transparency/log").json() == {"entries": []}

    e1 = _append("inc-1", "mitigated")
    e2 = _append("inc-1", "closed")
    head = c.get("/transparency/head").json()
    assert head["seq"] == 2 and head["prev_entry_hash"] == e2["entry_hash"]
    assert "recent_pairs" not in head                                 # internal idempotency cache not exposed

    entries = c.get("/transparency/log").json()["entries"]
    assert [e["seq"] for e in entries] == [1, 2]
    assert entries[1]["prev_entry_hash"] == e1["entry_hash"]          # the link the auditor recomputes
    assert entries[0]["entry_hash"] == e1["entry_hash"]
    # range query ?from=&to=
    assert [e["seq"] for e in c.get("/transparency/log?from=2&to=2").json()["entries"]] == [2]
