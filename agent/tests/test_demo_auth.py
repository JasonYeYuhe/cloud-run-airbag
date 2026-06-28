"""The /demo/* ACTION endpoints must be gated by AIRBAG_DEMO_TOKEN once set, while
the read-only dashboard + /health stay public. Guards against public PR/cost spam."""
import pytest
from fastapi.testclient import TestClient

import app as appmod
from autosre import config


@pytest.fixture
def client():
    return TestClient(appmod.app)


def test_open_when_token_unset(client, monkeypatch):
    monkeypatch.setattr(config, "DEMO_TOKEN", "")
    assert client.post("/demo/reset").status_code == 200  # no token needed


def test_gated_when_token_set(client, monkeypatch):
    monkeypatch.setattr(config, "DEMO_TOKEN", "s3cret")
    assert client.post("/demo/reset").status_code == 401            # missing
    assert client.post("/demo/reset",
                       headers={"x-airbag-demo-token": "nope"}).status_code == 401  # wrong
    assert client.post("/demo/reset",
                       headers={"x-airbag-demo-token": "s3cret"}).status_code == 200  # header ok
    assert client.post("/demo/reset?token=s3cret").status_code == 200  # query deep-link ok


def test_trigger_is_gated(client, monkeypatch):
    monkeypatch.setattr(config, "DEMO_TOKEN", "s3cret")
    # 401 fires in the dependency, before any heal/Gemini work is scheduled.
    assert client.post("/demo/trigger").status_code == 401


def test_public_endpoints_never_gated(client, monkeypatch):
    monkeypatch.setattr(config, "DEMO_TOKEN", "s3cret")
    assert client.get("/health").status_code == 200
    assert client.get("/").status_code == 200  # dashboard is watch-only public


def test_fails_closed_on_gcp_when_token_unset(client, monkeypatch):
    # a blank token on the public gcp service must REFUSE (503), never silently serve
    monkeypatch.setattr(config, "BACKEND", "gcp")
    monkeypatch.setattr(config, "DEMO_TOKEN", "")
    assert client.post("/demo/reset").status_code == 503

