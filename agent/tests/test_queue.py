"""Durable work queue (AIRBAG_QUEUE): flag isolation (inproc default = no-op for the demo), the
per-incident heal idempotency the queue relies on, and the Cloud-Tasks-facing worker's auth +
allowlist. conftest pins memory store + mock backend."""
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

import app as appmod
from autosre import config, queue
from autosre.backends import mock
from autosre.state_machine import run_self_heal


def test_inproc_enqueue_uses_background_tasks(monkeypatch):
    monkeypatch.setattr(config, "QUEUE_BACKEND", "inproc")
    bt = MagicMock()
    mode = queue.enqueue_heal(bt, "inc1", "airbag-target")
    assert mode == "inproc"
    bt.add_task.assert_called_once()                       # routed to FastAPI BackgroundTasks
    args = bt.add_task.call_args[0]
    assert args[1] == "inc1" and args[2] == "airbag-target"


def test_run_self_heal_drops_duplicate(monkeypatch):
    mock.reset()
    config.GEMINI_API_KEY = ""
    r1 = run_self_heal("dup-1", "airbag-target")
    assert r1["status"] in ("mitigated", "escalated", "noop", "observed")
    r2 = run_self_heal("dup-1", "airbag-target")           # same incident_id -> already done
    assert r2["status"] == "duplicate"                     # at-least-once redelivery is a no-op


def test_run_heal_worker_auth_and_allowlist(monkeypatch):
    monkeypatch.setattr(config, "INTERNAL_TOKEN", "itok")
    monkeypatch.setattr(appmod, "run_self_heal",
                        lambda iid, svc: {"status": "mitigated", "incident_id": iid})
    c = TestClient(appmod.app)
    assert c.post("/internal/run-heal", json={"incident_id": "x"}).status_code in (401, 403)  # no token
    h = {"x-airbag-internal-token": "itok"}
    assert c.post("/internal/run-heal", json={"incident_id": "x", "service": "evil"},
                  headers=h).status_code == 400                                                # allowlist
    assert c.post("/internal/run-heal", json={}, headers=h).status_code == 400                 # no incident_id
    ok = c.post("/internal/run-heal", json={"incident_id": "x"}, headers=h)
    assert ok.status_code == 200 and ok.json()["status"] == "mitigated"
