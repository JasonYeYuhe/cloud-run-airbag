"""Phase 1.3 TDD — the read-only poll + audit loop against a FAKE agent (injected fetch).

Focus: the loop attests every listed incident and FAILS OPEN on every hostile/broken fetch path
(unreachable, non-200, non-JSON, tampered) — the auditor's whole job is honest verdicts on an
untrusted, possibly-compromised agent. Uses a local agent key; the auditor counter-sign is exercised
in test_attestation, so most cases here use an unsigned (None) counter-signer and assert the VERDICT.
"""
import base64
import hashlib
import json

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

import poller
import verify

_AGENT_KEY = ("projects/airbag-hack-260628/locations/asia-northeast1/keyRings/airbag/"
              "cryptoKeys/airbag-proof/cryptoKeyVersions/1")
_URL = "https://airbag-agent.example.run.app"
_NONE_SIGNER = lambda digest: None            # noqa: E731 — unsigned attestations (focus on the verdict)


def _bundle(incident_id: str) -> dict:
    return {"incident_id": incident_id, "service": "airbag-target", "status": "mitigated",
            "decision": {"action": "ROLLBACK", "confidence": 0.7, "reasoning": "gate FAIL",
                         "_source": "heuristic"},
            "recovery": {"error_before": 0.1, "error_after": 0.0, "rolled_back_to": "svc-good",
                         "restored_to": None, "recovery_seconds": 42.0},
            "transitions": [{"stage": "MITIGATED", "ts": 1_783_094_600.0}]}


def _sign_with(priv, incident_id: str) -> bytes:
    """Serve-bytes of a heal proof for `incident_id` signed by `priv` (agent-style: sign canonical)."""
    bundle = _bundle(incident_id)
    canonical = json.dumps(bundle, sort_keys=True, separators=(",", ":"), default=str)
    digest = "sha256:" + hashlib.sha256(canonical.encode()).hexdigest()
    der = priv.sign(canonical.encode(), ec.ECDSA(hashes.SHA256()))
    proof = {"bundle": bundle, "digest": digest,
             "signature": {"algorithm": "EC_SIGN_P256_SHA256", "key": _AGENT_KEY,
                           "signature": base64.b64encode(der).decode()}}
    return json.dumps(proof).encode()


def _signed_proof_bytes(incident_id: str):
    """Return (raw_bytes, agent_pem) for a validly signed heal proof of `incident_id` (fresh key)."""
    priv = ec.generate_private_key(ec.SECP256R1())
    pem = priv.public_key().public_bytes(serialization.Encoding.PEM,
                                         serialization.PublicFormat.SubjectPublicKeyInfo)
    return _sign_with(priv, incident_id), pem


def _fake_fetch(proof_bytes_by_id: dict, *, ids=None, list_raises=False):
    """A fake agent: /incidents -> the id list; /incidents/{id}/proof -> its bytes (404 if unknown)."""
    listed = list(proof_bytes_by_id) if ids is None else ids

    def _fetch(url: str):
        if "/incidents?" in url or url.rstrip("/").endswith("/incidents"):
            if list_raises:
                raise ConnectionError("agent unreachable")
            return json.dumps({"incidents": [{"incident_id": i} for i in listed]}).encode(), 200
        for iid, raw in proof_bytes_by_id.items():
            if url.endswith(f"/incidents/{iid}/proof"):
                return raw, 200
        return b'{"detail":"unknown incident"}', 404

    return _fetch


def _now():
    return 1_783_100_000.0


# --- happy path -------------------------------------------------------------------------------------
def test_poll_once_attests_each_listed_incident():
    priv = ec.generate_private_key(ec.SECP256R1())   # one key signs two incidents -> one pem verifies both
    pem = priv.public_key().public_bytes(serialization.Encoding.PEM,
                                         serialization.PublicFormat.SubjectPublicKeyInfo)
    fetch = _fake_fetch({"inc-A": _sign_with(priv, "inc-A"), "inc-B": _sign_with(priv, "inc-B")})
    res = poller.poll_once(fetch=fetch, agent_url=_URL, expected_pem=pem, expected_key=_AGENT_KEY,
                           signer=_NONE_SIGNER, now=_now)
    assert set(res) == {"inc-A", "inc-B"}
    assert res["inc-A"]["bundle"]["tri_state"] == verify.SIGNED_VERIFIED
    assert res["inc-B"]["bundle"]["tri_state"] == verify.SIGNED_VERIFIED
    assert res["inc-A"]["bundle"]["fetch"]["http_status"] == 200


def test_audit_incident_binds_the_exact_served_bytes():
    raw_a, pem = _signed_proof_bytes("inc-A")
    env = poller.audit_incident("inc-A", fetch=_fake_fetch({"inc-A": raw_a}), agent_url=_URL,
                                expected_pem=pem, expected_key=_AGENT_KEY, signer=_NONE_SIGNER,
                                verified_at=_now())
    assert env["bundle"]["fetch"]["raw_fetched_digest"] == "sha256:" + hashlib.sha256(raw_a).hexdigest()
    assert env["bundle"]["tri_state"] == verify.SIGNED_VERIFIED


# --- fail-open on every broken fetch path -----------------------------------------------------------
def test_unreachable_agent_is_fail_not_crash():
    def _boom(url):
        raise ConnectionError("connection refused")

    env = poller.audit_incident("inc-A", fetch=_boom, agent_url=_URL, expected_pem=None,
                                expected_key=_AGENT_KEY, signer=_NONE_SIGNER, verified_at=_now())
    assert env["bundle"]["tri_state"] == verify.FAIL
    assert env["bundle"]["fetch"]["http_status"] == 0        # 0 == the fetch itself failed


def test_404_unknown_incident_is_fail():
    env = poller.audit_incident("inc-ghost", fetch=_fake_fetch({}), agent_url=_URL, expected_pem=None,
                                expected_key=_AGENT_KEY, signer=_NONE_SIGNER, verified_at=_now())
    assert env["bundle"]["tri_state"] == verify.FAIL
    assert env["bundle"]["fetch"]["http_status"] == 404


def test_non_json_body_is_fail_not_crash():
    def _html(url):
        return b"<html>500 Internal Server Error</html>", 200

    env = poller.audit_incident("inc-A", fetch=_html, agent_url=_URL, expected_pem=None,
                                expected_key=_AGENT_KEY, signer=_NONE_SIGNER, verified_at=_now())
    assert env["bundle"]["tri_state"] == verify.FAIL


def test_tampered_served_proof_is_fail():
    raw_a, pem = _signed_proof_bytes("inc-A")
    proof = json.loads(raw_a)
    proof["bundle"]["recovery"]["rolled_back_to"] = "attacker-swapped"   # tamper the served bytes
    tampered = json.dumps(proof).encode()
    env = poller.audit_incident("inc-A", fetch=_fake_fetch({"inc-A": tampered}), agent_url=_URL,
                                expected_pem=pem, expected_key=_AGENT_KEY, signer=_NONE_SIGNER,
                                verified_at=_now())
    assert env["bundle"]["tri_state"] == verify.FAIL
    assert env["bundle"]["integrity_ok"] is False


def test_list_incidents_failure_yields_empty_poll():
    res = poller.poll_once(fetch=_fake_fetch({}, list_raises=True), agent_url=_URL, expected_pem=None,
                           expected_key=_AGENT_KEY, signer=_NONE_SIGNER, now=_now)
    assert res == {}


# --- confirmed review findings (Phase 1.3): hostile /incidents list must never wedge the loop -------
def test_non_string_incident_id_is_skipped_not_crash():
    """BLOCKER fix: a non-string incident_id (list/dict/None/number) would become an unhashable dict
    key and crash the WHOLE poll cycle forever — permanent suppression. It must be skipped instead."""
    priv = ec.generate_private_key(ec.SECP256R1())
    pem = priv.public_key().public_bytes(serialization.Encoding.PEM,
                                         serialization.PublicFormat.SubjectPublicKeyInfo)
    fetch = _fake_fetch({"inc-A": _sign_with(priv, "inc-A")},
                        ids=["inc-A", [1, 2, 3], {"a": 1}, None, 42])
    res = poller.poll_once(fetch=fetch, agent_url=_URL, expected_pem=pem, expected_key=_AGENT_KEY,
                           signer=_NONE_SIGNER, now=_now)
    assert set(res) == {"inc-A"}                              # every non-string id skipped, no crash
    assert res["inc-A"]["bundle"]["tri_state"] == verify.SIGNED_VERIFIED


def test_oversized_incident_list_is_truncated_to_limit():
    """The agent may ignore ?limit=N; the auditor enforces the cap locally so a huge list can't wedge
    the sequential poll cycle (a fail-silent DoS of the watchdog)."""
    fetch = _fake_fetch({}, ids=[f"inc-{n}" for n in range(100)])
    res = poller.poll_once(fetch=fetch, agent_url=_URL, expected_pem=None, expected_key=_AGENT_KEY,
                           signer=_NONE_SIGNER, now=_now, limit=25)
    assert len(res) == 25


def test_one_poison_incident_does_not_abort_the_cycle(monkeypatch):
    """Defense-in-depth: even if an audit raises unexpectedly, the rest of the cycle still completes."""
    real = poller.audit_incident

    def _sometimes_raise(iid, **kw):
        if iid == "inc-bad":
            raise RuntimeError("unexpected audit error")
        return real(iid, **kw)

    monkeypatch.setattr(poller, "audit_incident", _sometimes_raise)
    res = poller.poll_once(fetch=_fake_fetch({}, ids=["inc-bad", "inc-good"]), agent_url=_URL,
                           expected_pem=None, expected_key=_AGENT_KEY, signer=_NONE_SIGNER, now=_now)
    assert "inc-good" in res and "inc-bad" not in res


def test_httpx_fetch_returns_body_and_status():
    import httpx
    t = httpx.MockTransport(lambda req: httpx.Response(200, content=b'{"ok":true}'))
    body, status = poller.httpx_fetch(5.0, transport=t)("https://agent.example/x")
    assert body == b'{"ok":true}' and status == 200


def test_httpx_fetch_caps_oversized_body_to_prevent_oom():
    import httpx
    import pytest
    big = b"x" * (2 * 1024 * 1024)                            # 2 MB body, cap at 1 MB
    t = httpx.MockTransport(lambda req: httpx.Response(200, content=big))
    fetch = poller.httpx_fetch(5.0, max_bytes=1_000_000, transport=t)
    with pytest.raises(Exception):
        fetch("https://agent.example/incidents/inc-A/proof")
