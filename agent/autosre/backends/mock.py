"""In-memory backend (CI/tests). Simulates a bad revision serving, then recovery."""
from __future__ import annotations

_STATE = {"rolled_back": False}


def reset() -> None:
    _STATE["rolled_back"] = False


def list_cloud_run_revisions(service: str, region: str) -> dict:
    rb = _STATE["rolled_back"]
    return {"service": service, "revisions": [
        {"name": f"{service}-00002-bad", "ready": True,
         "traffic_percent": 0 if rb else 100, "create_time": "2026-06-28T00:00:00Z"},
        {"name": f"{service}-00001-good", "ready": True,
         "traffic_percent": 100 if rb else 0, "create_time": "2026-06-27T22:00:00Z"},
    ]}


def query_error_rate(service: str, region: str, window_minutes: int = 5,
                     since_epoch: float | None = None) -> dict:
    rate = 0.0 if _STATE["rolled_back"] else 0.12
    return {"service": service, "error_rate": rate, "total_requests": 200,
            "window_minutes": window_minutes}


def synthetic_probe(service: str, path: str = "/healthz") -> dict:
    ok = _STATE["rolled_back"]
    return {"ok": ok, "path": path, "status": 200 if ok else 503}


def rollback_traffic_to_revision(service: str, region: str, revision: str) -> dict:
    _STATE["rolled_back"] = True
    return {"status": "success", "service": service, "active_revision": revision}


def restore_traffic_to_latest(service: str, region: str) -> dict:
    _STATE["rolled_back"] = False
    return {"status": "success", "service": service, "active_revision": "LATEST"}
