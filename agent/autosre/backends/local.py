"""Local backend — real HTTP against a locally-running target-app.

This makes the whole self-heal loop genuinely real without any cloud:
  - error rate  = sample real GETs to /api/orders and count 5xx
  - probe       = real GET /healthz
  - rollback    = POST /__fault/off  (simulates shifting traffic to the healthy revision)
The same agent code runs unchanged against real Cloud Run via the gcp backend.
"""
from __future__ import annotations

import httpx

from .. import config


def _url(path: str) -> str:
    return config.TARGET_BASE_URL.rstrip("/") + path


def _sample(n: int | None = None) -> tuple[int, int]:
    n = n or config.ERROR_SAMPLE_N
    errs = total = 0
    with httpx.Client(timeout=3.0) as c:
        for _ in range(n):
            total += 1
            try:
                if c.get(_url("/api/orders")).status_code >= 500:
                    errs += 1
            except Exception:
                errs += 1
    return errs, total


def list_cloud_run_revisions(service: str, region: str) -> dict:
    errs, _ = _sample(3)
    faulted = errs > 0
    return {"service": service, "revisions": [
        {"name": f"{service}-00002-bad", "ready": True,
         "traffic_percent": 100 if faulted else 0, "create_time": "2026-06-28T00:00:00Z"},
        {"name": f"{service}-00001-good", "ready": True,
         "traffic_percent": 0 if faulted else 100, "create_time": "2026-06-27T22:00:00Z"},
        # the "deployed fix" so the dashboard's Verify & Undo can CLOSE end-to-end locally
        # (far-future create_time -> always counts as "deployed after the rollback")
        {"name": f"{service}-00003-fix", "ready": True,
         "traffic_percent": 0, "create_time": "2099-01-01T00:00:00Z"},
    ]}


def query_error_rate(service: str, region: str, window_minutes: int = 5,
                     since_epoch: float | None = None) -> dict:
    errs, total = _sample()
    rate = round(errs / total, 3) if total else 0.0
    return {"service": service, "error_rate": rate, "total_requests": total,
            "errors": errs, "window_minutes": window_minutes}


def synthetic_probe(service: str, path: str = "/healthz") -> dict:
    try:
        with httpx.Client(timeout=3.0) as c:
            r = c.get(_url(path))
        return {"ok": r.status_code == 200, "path": path, "status": r.status_code}
    except Exception as e:
        return {"ok": False, "path": path, "status": 0, "error": str(e)}


def rollback_traffic_to_revision(service: str, region: str, revision: str) -> dict:
    with httpx.Client(timeout=3.0) as c:
        c.post(_url("/__fault/off"))
    return {"status": "success", "service": service, "active_revision": revision,
            "note": "local: fault cleared = traffic shifted to healthy revision"}


def restore_traffic_to_latest(service: str, region: str) -> dict:
    return {"status": "success", "service": service, "active_revision": "LATEST"}


# --- demo harness: toggle the runtime KeyError fault on the local target-app ---------
def break_target(service: str, region: str) -> dict:
    with httpx.Client(timeout=3.0) as c:
        c.post(_url("/__fault/bug"))
    return {"status": "success", "service": service,
            "active_revision": f"{service}-00002-bad", "fault": "bug"}


def reset_target(service: str, region: str) -> dict:
    with httpx.Client(timeout=3.0) as c:
        c.post(_url("/__fault/off"))
    return {"status": "success", "service": service,
            "active_revision": f"{service}-00001-good"}
