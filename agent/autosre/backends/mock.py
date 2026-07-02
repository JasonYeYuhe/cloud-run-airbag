"""In-memory backend (CI/tests). Simulates a bad revision serving, then recovery, and
(for the P1 transaction) a later fix revision deployed after the rollback."""
from __future__ import annotations

_STATE = {"rolled_back": False, "fix_deployed": False}


def reset() -> None:
    _STATE["rolled_back"] = False
    _STATE["fix_deployed"] = False


def deploy_fix() -> None:
    """Simulate the fix PR's CI deploying a new healthy revision after the rollback."""
    _STATE["fix_deployed"] = True


def list_cloud_run_revisions(service: str, region: str) -> dict:
    rb = _STATE["rolled_back"]
    revs = [
        {"name": f"{service}-00002-bad", "ready": True,
         "traffic_percent": 0 if rb else 100, "create_time": "2026-06-28T00:00:00Z"},
        {"name": f"{service}-00001-good", "ready": True,
         "traffic_percent": 100 if rb else 0, "create_time": "2026-06-27T22:00:00Z"},
    ]
    if _STATE["fix_deployed"]:
        # far-future create_time so it sorts as "created after the rollback"
        revs.insert(0, {"name": f"{service}-00003-fix", "ready": True,
                        "traffic_percent": 0, "create_time": "2099-01-01T00:00:00Z"})
    return {"service": service, "revisions": revs}


def query_error_rate(service: str, region: str, window_minutes: int = 5,
                     since_epoch: float | None = None) -> dict:
    rate = 0.0 if _STATE["rolled_back"] else 0.12
    return {"service": service, "error_rate": rate, "total_requests": 200,
            "window_minutes": window_minutes}


def fetch_error_logs(service: str, region: str, n: int = 10) -> list[str]:
    return ['Traceback (most recent call last):\n  File "main.py", line 55, in orders\n'
            "    ... total_revenue(ORDERS, buggy=True)\n  File \"main.py\", line 46, in total_revenue\n"
            "    return sum(o[key] for o in orders)\nKeyError: 'amount'"]


def sample_business_path(service: str, region: str, n: int = 20) -> dict:
    # bad revision serving -> total outage in the sample; healthy -> clean
    return {"errs": 0 if _STATE["rolled_back"] else n, "total": n}


def sample_latency_windows(service: str, region: str, windows: int = 4) -> list[dict]:
    # benign by default (no slow requests) — the mock heal is a 5xx scenario; latency tests inject data
    return [{"slow": 0, "total": 20} for _ in range(windows)]


def probe_revision_health(service: str, region: str, revision: str, n: int = 8) -> dict:
    # the mock's last-good revision is healthy and fast (the mock scenario is a bad DEPLOY,
    # not a dependency/latency coincidence)
    return {"errs": 0, "total": n, "slow": 0}


def synthetic_probe(service: str, path: str = "/healthz") -> dict:
    ok = _STATE["rolled_back"]
    # healthy revision responds fast; elapsed_ms lets the latency-aware verify confirm the SIGNAL
    return {"ok": ok, "path": path, "status": 200 if ok else 503,
            "elapsed_ms": 10.0 if ok else 0.0}


def rollback_traffic_to_revision(service: str, region: str, revision: str) -> dict:
    _STATE["rolled_back"] = True
    return {"status": "success", "service": service, "active_revision": revision}


# --- demo harness ---
def break_target(service: str, region: str, prefer: str = "bug") -> dict:
    _STATE["rolled_back"] = False  # bad revision serving 100% (error rate 0.12)
    return {"status": "success", "service": service,
            "active_revision": f"{service}-00002-bad", "fault": prefer}


def reset_target(service: str, region: str) -> dict:
    _STATE["rolled_back"] = True  # good revision serving
    return {"status": "success", "service": service,
            "active_revision": f"{service}-00001-good"}


def set_traffic_split(service: str, region: str, splits: dict, tag_revision: str | None = None) -> dict:
    _STATE["rolled_back"] = True  # any healthy revision serving -> error rate 0, probe ok
    return {"status": "success", "service": service, "traffic": dict(splits)}


def probe_candidate(service: str, region: str, revision: str) -> dict:
    ok = _STATE["rolled_back"]
    return {"ok": ok, "errors": 0 if ok else 1, "total": 1}
