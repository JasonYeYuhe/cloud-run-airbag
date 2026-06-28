"""Production webhook intake: Cloud Monitoring token + state + idempotency, and the Sentry
HMAC-SHA256 body signature. The heal itself is stubbed — this locks the INTAKE contract."""
import hashlib
import hmac as hmaclib

from fastapi.testclient import TestClient

import app as appmod
from autosre import config


def test_cloud_monitoring_intake(monkeypatch):
    monkeypatch.setattr(config, "WEBHOOK_TOKEN", "wh")
    monkeypatch.setattr(appmod, "run_self_heal", lambda *a, **k: None)  # don't run the real heal
    appmod._seen_incidents.clear()
    c = TestClient(appmod.app)

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
