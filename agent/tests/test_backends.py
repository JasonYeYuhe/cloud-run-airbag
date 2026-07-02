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


# --- Phase 0.4: optimistic-concurrency on traffic mutation (--max-instances 3 races) ---
class _FakeOp:
    def result(self, timeout=None):
        return None


class _FakeSvc:
    def __init__(self, etag):
        self.etag = etag
        self.traffic = None


def test_set_traffic_retries_on_etag_conflict(monkeypatch):
    """A concurrent writer bumps the etag -> the first update is rejected; _set_traffic must re-read
    a FRESH service (fresh etag) and re-apply the INTENDED targets, not blindly re-send a stale svc."""
    from google.api_core import exceptions as gax
    gets = {"n": 0}

    def fake_get(s, r):
        gets["n"] += 1
        return _FakeSvc(etag=f"etag-{gets['n']}")

    monkeypatch.setattr(gcp, "_get_service", fake_get)
    monkeypatch.setattr(gcp.time, "sleep", lambda *_: None)
    seen, calls = [], {"n": 0}

    class FakeClient:
        def update_service(self, service, update_mask):
            calls["n"] += 1
            seen.append((service.etag, service.traffic))
            if calls["n"] == 1:
                raise gax.Aborted("concurrent modification")  # stale etag
            return _FakeOp()

    monkeypatch.setattr(run_v2, "ServicesClient", lambda: FakeClient())
    gcp._set_traffic("svc", "r", ["T"])
    assert calls["n"] == 2, "should retry once after the etag conflict"
    assert gets["n"] == 2, "must re-read a FRESH service before retrying (new etag)"
    assert seen[0][0] != seen[1][0], "the retry must use the fresh etag, not the stale one"
    assert seen[1][1] == ["T"], "the intended targets must be re-applied on the retry"


def test_probe_revision_health_drops_transport_errors(monkeypatch):
    """SAFETY (causal): an UNREACHABLE target (cold start on the scaled-to-zero last-good revision /
    timeout) must NOT count as a failure — else it would falsely COINCIDENT-block a legit rollback."""
    svc = SimpleNamespace(traffic=[], traffic_statuses=[
        SimpleNamespace(tag=gcp._CAUSAL_TAG, uri="https://tag---svc.run.app")])
    monkeypatch.setattr(gcp, "_get_service", lambda s, r: svc)
    monkeypatch.setattr(gcp, "_set_traffic", lambda *a, **k: None)

    def _raise(self, url, **k):
        raise httpx.ConnectError("unreachable")
    monkeypatch.setattr(httpx.Client, "get", _raise)
    assert gcp.probe_revision_health("svc", "r", "svc-good", n=6) == {"errs": 0, "total": 0, "slow": 0}


def test_probe_revision_health_counts_5xx(monkeypatch):
    """A reached 5xx STATUS is genuine evidence the target served-and-broke → counted."""
    svc = SimpleNamespace(traffic=[], traffic_statuses=[
        SimpleNamespace(tag=gcp._CAUSAL_TAG, uri="https://tag---svc.run.app")])
    monkeypatch.setattr(gcp, "_get_service", lambda s, r: svc)
    monkeypatch.setattr(gcp, "_set_traffic", lambda *a, **k: None)
    monkeypatch.setattr(httpx.Client, "get", lambda self, url, **k: _Resp(500))
    assert gcp.probe_revision_health("svc", "r", "svc-good", n=6) == {"errs": 6, "total": 6, "slow": 0}


def test_probe_revision_health_times_slow_successes(monkeypatch):
    """v4 Phase 2: a SUCCESSFUL response slower than the latency SLO is counted in `slow` (a 5xx is
    an err, never double-counted as slow) — the axis the latency-keyed causal veto gates on. The
    WARMUP RINSE is pinned too: one extra uncounted request precedes the n counted probes, so a
    scaled-to-zero target's cold start can't masquerade as latency evidence (review MAJOR)."""
    svc = SimpleNamespace(traffic=[], traffic_statuses=[
        SimpleNamespace(tag=gcp._CAUSAL_TAG, uri="https://tag---svc.run.app")])
    monkeypatch.setattr(gcp, "_get_service", lambda s, r: svc)
    monkeypatch.setattr(gcp, "_set_traffic", lambda *a, **k: None)
    monkeypatch.setattr(gcp.config, "LATENCY_SLO_ABS_MS", 800.0)   # pin: env must not flip the test
    calls = {"n": 0}

    def fake_get(self, url, **k):
        calls["n"] += 1
        return _Resp(200)
    monkeypatch.setattr(httpx.Client, "get", fake_get)
    tick = {"t": 0.0}

    def fake_monotonic():
        tick["t"] += 1.0        # 2 monotonic() calls per COUNTED request, +1.0 apart -> each
        return tick["t"]        # request appears to take 1s (> the 800ms SLO pinned above)
    monkeypatch.setattr(gcp.time, "monotonic", fake_monotonic)
    assert gcp.probe_revision_health("svc", "r", "svc-good", n=4) == {"errs": 0, "total": 4, "slow": 4}
    assert calls["n"] == 5, "expected 1 untimed warmup rinse + 4 counted probes"


def test_set_traffic_raises_after_exhausting_retries(monkeypatch):
    """A persistent conflict surfaces (so the heal releases its lease + a retry re-runs) and does
    NOT loop forever."""
    from google.api_core import exceptions as gax
    gets = {"n": 0}

    def fake_get(s, r):
        gets["n"] += 1
        return _FakeSvc("etag")

    monkeypatch.setattr(gcp, "_get_service", fake_get)
    monkeypatch.setattr(gcp.time, "sleep", lambda *_: None)

    class AlwaysConflict:
        def update_service(self, service, update_mask):
            raise gax.FailedPrecondition("etag mismatch")

    monkeypatch.setattr(run_v2, "ServicesClient", lambda: AlwaysConflict())
    with pytest.raises(gax.FailedPrecondition):
        gcp._set_traffic("svc", "r", ["T"])
    assert gets["n"] == gcp._TRAFFIC_MAX_ATTEMPTS, "bounded retries, no infinite loop"
