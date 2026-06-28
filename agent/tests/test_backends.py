"""Backend logic that the heal depends on: the local backend's HTTP sampling, and the gcp
backend's LATEST→newest traffic resolution + revision-env parsing (the only production path)."""
import datetime
from types import SimpleNamespace

import httpx
import pytest

from autosre.backends import local


class _Resp:
    def __init__(self, code):
        self.status_code = code


def test_local_error_rate_counts_5xx(monkeypatch):
    codes = iter([500, 500] + [200] * 20)
    monkeypatch.setattr(httpx.Client, "get", lambda self, url, **k: _Resp(next(codes, 200)))
    r = local.query_error_rate("svc", "r")
    assert r["errors"] == 2 and r["total_requests"] == 12  # config.ERROR_SAMPLE_N
    assert r["error_rate"] == round(2 / 12, 3)


def test_local_faulted_revision_serves(monkeypatch):
    monkeypatch.setattr(httpx.Client, "get", lambda self, url, **k: _Resp(500))
    revs = local.list_cloud_run_revisions("svc", "r")["revisions"]
    assert revs[0]["name"].endswith("-bad") and revs[0]["traffic_percent"] == 100
    assert local.probe_candidate("svc", "r", "svc-00003-fix")["ok"] is False  # all 500 -> not ok


# --- gcp: LATEST resolution + env parsing (runs in CI where google-cloud-run is installed) ---
run_v2 = pytest.importorskip("google.cloud.run_v2")
from autosre.backends import gcp  # noqa: E402


def _rev(name, hour):
    return SimpleNamespace(
        name=f"projects/p/locations/r/services/s/revisions/{name}",
        conditions=[SimpleNamespace(type_="Ready", state=run_v2.Condition.State.CONDITION_SUCCEEDED)],
        create_time=datetime.datetime(2026, 6, 28, hour, 0, 0, tzinfo=datetime.timezone.utc))


def test_latest_traffic_resolves_to_newest_revision(monkeypatch):
    latest = SimpleNamespace(
        revision="", percent=100,
        type_=run_v2.TrafficTargetAllocationType.TRAFFIC_TARGET_ALLOCATION_TYPE_LATEST)
    svc = SimpleNamespace(traffic_statuses=[latest], uri="https://svc.run.app")
    revs = [_rev("svc-00002-new", 10), _rev("svc-00001-old", 9)]
    monkeypatch.setattr(gcp, "_get_service", lambda s, r: svc)
    monkeypatch.setattr(run_v2, "RevisionsClient",
                        lambda: SimpleNamespace(list_revisions=lambda parent: iter(revs)))
    out = gcp.list_cloud_run_revisions("s", "r")["revisions"]
    assert out[0]["name"] == "svc-00002-new" and out[0]["traffic_percent"] == 100  # LATEST→newest
    assert out[1]["traffic_percent"] == 0


def test_revision_env_parsing():
    rev = SimpleNamespace(containers=[SimpleNamespace(
        env=[SimpleNamespace(name="FAULT_MODE", value="bug")])])
    assert gcp._revision_env(rev) == {"FAULT_MODE": "bug"}
