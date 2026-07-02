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
        {"name": f"{service}-00002-bad", "ready": True, "irreversible": False,
         "traffic_percent": 100 if faulted else 0, "create_time": "2026-06-28T00:00:00Z"},
        {"name": f"{service}-00001-good", "ready": True, "irreversible": False,
         "traffic_percent": 0 if faulted else 100, "create_time": "2026-06-27T22:00:00Z"},
        # the "deployed fix" so the dashboard's Verify & Undo can CLOSE end-to-end locally
        # (far-future create_time -> always counts as "deployed after the rollback")
        {"name": f"{service}-00003-fix", "ready": True, "irreversible": False,
         "traffic_percent": 0, "create_time": "2099-01-01T00:00:00Z"},
    ]}


def query_error_rate(service: str, region: str, window_minutes: int = 5,
                     since_epoch: float | None = None) -> dict:
    errs, total = _sample()
    rate = round(errs / total, 3) if total else 0.0
    return {"service": service, "error_rate": rate, "total_requests": total,
            "errors": errs, "window_minutes": window_minutes}


def fetch_error_logs(service: str, region: str, n: int = 10) -> list[str]:
    # the local single-process target doesn't expose a central log; return a representative trace
    return ['Traceback (most recent call last):\n  File "main.py", line 55, in orders\n'
            '    return {"orders": ORDERS, "revenue": total_revenue(ORDERS, buggy=True)}\n'
            '  File "main.py", line 46, in total_revenue\n    return sum(o[key] for o in orders)\n'
            "KeyError: 'amount'"]


def sample_business_path(service: str, region: str, n: int = 20) -> dict:
    errs, total = _sample(n)
    return {"errs": errs, "total": total}


def synthetic_probe(service: str, path: str = "/healthz") -> dict:
    import time
    try:
        with httpx.Client(timeout=5.0) as c:  # > the slow-fault delay so a slow SUCCESS is timed, not dropped
            t0 = time.monotonic()
            r = c.get(_url(path))
            elapsed_ms = (time.monotonic() - t0) * 1000.0
        return {"ok": r.status_code == 200, "path": path, "status": r.status_code,
                "elapsed_ms": round(elapsed_ms, 1)}
    except Exception as e:
        return {"ok": False, "path": path, "status": 0, "error": str(e)}


def rollback_traffic_to_revision(service: str, region: str, revision: str) -> dict:
    with httpx.Client(timeout=3.0) as c:
        c.post(_url("/__fault/off"))
    return {"status": "success", "service": service, "active_revision": revision,
            "note": "local: fault cleared = traffic shifted to healthy revision"}


# --- demo harness: toggle the runtime KeyError fault on the local target-app ---------
def break_target(service: str, region: str, prefer: str = "bug") -> dict:
    with httpx.Client(timeout=3.0) as c:
        c.post(_url(f"/__fault/{prefer}"))   # 'bug' (KeyError) or 'slow' (latency regression)
    return {"status": "success", "service": service,
            "active_revision": f"{service}-00002-bad", "fault": prefer}


def reset_target(service: str, region: str) -> dict:
    with httpx.Client(timeout=3.0) as c:
        c.post(_url("/__fault/off"))
    return {"status": "success", "service": service,
            "active_revision": f"{service}-00001-good"}


def set_traffic_split(service: str, region: str, splits: dict, tag_revision: str | None = None) -> dict:
    # local target is a single process — model the canary by clearing the fault once the fix
    # gets any traffic (so the probe/error-rate read healthy at each stage).
    with httpx.Client(timeout=3.0) as c:
        c.post(_url("/__fault/off"))
    return {"status": "success", "service": service, "traffic": dict(splits)}


def probe_candidate(service: str, region: str, revision: str, n: int = 5) -> dict:
    errs, total = _sample(n)  # the local target is a single process; sample the business path
    return {"ok": errs == 0, "errors": errs, "total": total}


def probe_revision_health(service: str, region: str, revision: str, n: int = 8) -> dict:
    # the local target is a single process (no per-revision URL), so the "rollback target" health
    # equals the current business-path sample — enough to exercise the causal check locally.
    # v4: per-request timing so the latency axis ({slow}) is real here too (a slow SUCCESS counts;
    # 5xx and slow stay disjoint, mirroring the gcp probe + sample_latency_windows). The timeout is
    # DELIBERATELY 10s (was 3s via _sample): a slow success must be TIMED as slow, not misread as
    # an err — a 3-10s response now counts on the latency axis instead of the 5xx axis.
    import time
    slo_ms = config.LATENCY_SLO_ABS_MS
    errs = total = slow = 0
    with httpx.Client(timeout=10.0) as c:
        for _ in range(n):
            total += 1
            t0 = time.monotonic()
            try:
                r = c.get(_url("/api/orders"))
                dt_ms = (time.monotonic() - t0) * 1000.0
                if r.status_code >= 500:
                    errs += 1
                elif dt_ms > slo_ms:
                    slow += 1
            except Exception:  # noqa: BLE001 — local single process: unreachable IS evidence
                errs += 1
    return {"errs": errs, "total": total, "slow": slow}
