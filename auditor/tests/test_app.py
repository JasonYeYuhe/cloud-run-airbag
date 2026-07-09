"""Phase 1.3 TDD — the auditor FastAPI service (routes + status page).

No network: config.AGENT_PROOF_URL is empty in tests, so the lifespan starts NO poll loop; we inject
_STATE directly and exercise the read surfaces. Renders all four tri-states so the money-shot page is
pinned (a flip must be visible on camera).
"""
import app as auditor_app
from fastapi.testclient import TestClient

client = TestClient(auditor_app.app)


def _env(iid: str, tri: str, *, signed: bool = True) -> dict:
    b = {"attestation_version": "airbag.attestation/v1", "incident_id": iid, "tri_state": tri,
         "integrity_ok": tri != "FAIL", "signature_ok": tri == "SIGNED-VERIFIED",
         "signer_pinned": tri == "SIGNED-VERIFIED", "signed_expected": tri == "DEGRADED",
         "expected_key": "projects/p/.../cryptoKeyVersions/1",
         "verified_signer": "projects/p/.../cryptoKeyVersions/1" if tri == "SIGNED-VERIFIED" else None,
         "subject_digest": "sha256:abc", "verified_at": 1.0,
         "fetch": {"agent_url": "https://agent.example", "requested_incident_id": iid,
                   "http_status": 200, "raw_fetched_digest": "sha256:x", "incident_id_match": True}}
    env = {"bundle": b, "digest": "sha256:d", "note": "attestation"}
    if signed:
        env["signature"] = {"algorithm": "EC_SIGN_P256_SHA256", "key": "auditor", "signature": "c2ln"}
    return env


def _seed(**by_id):
    auditor_app._STATE.update(attestations=dict(by_id), last_poll_at=1_783_100_000.0, error=None)


def _clear():
    auditor_app._STATE.update(attestations={}, last_poll_at=None, error=None)


def test_health():
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True and body["role"] == "auditor"
    assert "expected_agent_key" in body and "counter_signing" in body


def test_status_page_renders_all_four_tristates():
    _seed(**{"inc-A": _env("inc-A", "SIGNED-VERIFIED"), "inc-B": _env("inc-B", "FAIL"),
             "inc-C": _env("inc-C", "INTEGRITY-ONLY", signed=True),
             "inc-D": _env("inc-D", "DEGRADED")})
    r = client.get("/")
    assert r.status_code == 200 and "text/html" in r.headers["content-type"]
    body = r.text
    for token in ("SIGNED-VERIFIED", "FAIL", "INTEGRITY-ONLY", "DEGRADED",
                  "inc-A", "inc-B", "inc-C", "inc-D", "Airbag Auditor"):
        assert token in body, f"status page missing {token!r}"
    assert "http-equiv=\"refresh\"" in body        # auto-refresh so a flip lands on camera
    _clear()


def test_status_page_empty_state():
    _clear()
    r = client.get("/")
    assert r.status_code == 200 and "no attestations yet" in r.text


def test_status_page_escapes_hostile_agent_controlled_fields():
    """The auditor renders data fetched from a POSSIBLY-COMPROMISED agent (incident_id, subject_digest
    originate in the agent's published JSON). Those must be HTML-escaped or the money-shot status page
    is a stored-XSS vector viewed by judges/operators."""
    hostile = _env("x", "FAIL")
    hostile["bundle"]["incident_id"] = "<script>alert(1)</script>"
    hostile["bundle"]["subject_digest"] = 'sha256:"><img src=x onerror=alert(2)>'
    _seed(x=hostile)
    body = client.get("/").text
    assert "<script>alert(1)</script>" not in body and "<img src=x onerror" not in body
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in body      # escaped, rendered as text
    _clear()


def test_attestations_json():
    _seed(**{"inc-A": _env("inc-A", "SIGNED-VERIFIED")})
    r = client.get("/attestations")
    body = r.json()
    assert body["count"] == 1 and body["attestations"][0]["bundle"]["incident_id"] == "inc-A"
    _clear()


def test_attestation_for_found_and_missing():
    _seed(**{"inc-A": _env("inc-A", "FAIL")})
    assert client.get("/attestations/inc-A").json()["bundle"]["tri_state"] == "FAIL"
    assert client.get("/attestations/inc-nope").status_code == 404
    _clear()
