"""Production webhook intake: Cloud Monitoring token + state + idempotency, and the Sentry
HMAC-SHA256 body signature. The heal itself is stubbed — this locks the INTAKE contract."""
import base64
import hashlib
import hmac as hmaclib

from fastapi.testclient import TestClient

import app as appmod
from autosre import config


def _basic(token: str, user: str = "airbag") -> dict:
    return {"authorization": "Basic " + base64.b64encode(f"{user}:{token}".encode()).decode()}


def test_cloud_monitoring_intake(monkeypatch):
    monkeypatch.setattr(config, "WEBHOOK_TOKEN", "wh")
    monkeypatch.setattr(appmod, "run_self_heal", lambda *a, **k: None)  # don't run the real heal
    c = TestClient(appmod.app)  # dedup store reset by the conftest autouse fixture

    assert c.post("/alerts/cloud-monitoring",
                  json={"incident": {"state": "open"}}).status_code == 401          # no token
    ok = c.post("/alerts/cloud-monitoring?token=wh",
                json={"incident": {"incident_id": "i1", "state": "open"}})
    assert ok.status_code == 202 and ok.json()["status"] == "accepted"
    dup = c.post("/alerts/cloud-monitoring?token=wh",
                 json={"incident": {"incident_id": "i1", "state": "open"}})
    assert dup.json()["status"] == "duplicate"                                       # idempotent
    closed = c.post("/alerts/cloud-monitoring?token=wh",
                    json={"incident": {"incident_id": "i2", "state": "closed"}})
    assert closed.json()["status"] == "ignored"                                      # not 'open'


def test_cloud_monitoring_basicauth_header(monkeypatch):
    """Phase 0.6: the alert token rides in the Authorization header (webhook_basicauth) or
    x-airbag-token — not the URL. A bad password gets a 401 + RFC2617 challenge."""
    monkeypatch.setattr(config, "WEBHOOK_TOKEN", "wh")
    monkeypatch.setattr(appmod, "run_self_heal", lambda *a, **k: None)
    c = TestClient(appmod.app)

    ok = c.post("/alerts/cloud-monitoring", headers=_basic("wh"),
                json={"incident": {"incident_id": "b1", "state": "open"}})
    assert ok.status_code == 202 and ok.json()["status"] == "accepted"   # Basic auth header
    ok2 = c.post("/alerts/cloud-monitoring", headers={"x-airbag-token": "wh"},
                 json={"incident": {"incident_id": "b2", "state": "open"}})
    assert ok2.status_code == 202                                         # x-airbag-token header
    bad = c.post("/alerts/cloud-monitoring", headers=_basic("nope"),
                 json={"incident": {"incident_id": "b3", "state": "open"}})
    assert bad.status_code == 401 and "Basic" in bad.headers.get("www-authenticate", "")


def test_internal_endpoints_are_header_only(monkeypatch):
    """Phase 0.6: a ?token= query no longer authenticates the destructive machine control plane
    (it would persist the secret in request/audit logs). Headers still work; CI/Cloud Tasks use them."""
    monkeypatch.setattr(config, "WEBHOOK_TOKEN", "wh")
    monkeypatch.setattr(config, "INTERNAL_TOKEN", "it")
    monkeypatch.setattr(appmod, "complete_rollback", lambda *a, **k: {"status": "noop"})
    c = TestClient(appmod.app)
    assert c.post("/internal/complete-rollback?token=wh", json={}).status_code == 401   # query rejected
    assert c.post("/internal/complete-rollback", json={},
                  headers={"x-airbag-token": "wh"}).status_code in (200, 422)            # header ok
    assert c.post("/internal/run-heal?token=it",
                  json={"incident_id": "x"}).status_code in (401, 403)                   # query rejected


def test_sentry_hmac(monkeypatch):
    monkeypatch.setattr(config, "SENTRY_SECRET", "s3cr3t")
    monkeypatch.setattr(appmod, "run_self_heal", lambda *a, **k: None)
    c = TestClient(appmod.app)
    body = b'{"event":"error"}'
    sig = hmaclib.new(b"s3cr3t", body, hashlib.sha256).hexdigest()
    assert c.post("/alerts/sentry", content=body,
                  headers={"sentry-hook-signature": sig}).status_code == 202
    assert c.post("/alerts/sentry", content=body,
                  headers={"sentry-hook-signature": "bad"}).status_code == 401
