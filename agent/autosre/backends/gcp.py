"""GCP backend — real Cloud Run + Cloud Logging.

Wired to the verified API shapes (run_v2 traffic split, logging 5xx count). It is
behind AIRBAG_BACKEND=gcp and untested until `gcloud auth` + a billing-enabled
project exist — validate against the live project before the demo (see docs/PLAN.md).
"""
from __future__ import annotations

import datetime

import httpx

from .. import config


def _service_path(service: str, region: str) -> str:
    return f"projects/{config.GCP_PROJECT}/locations/{region}/services/{service}"


def _get_service(service: str, region: str):
    from google.cloud import run_v2
    return run_v2.ServicesClient().get_service(name=_service_path(service, region))


def list_cloud_run_revisions(service: str, region: str) -> dict:
    from google.cloud import run_v2

    svc = _get_service(service, region)
    traffic = {t.revision: t.percent for t in svc.traffic_statuses if t.revision}
    revs = []
    for r in run_v2.RevisionsClient().list_revisions(parent=_service_path(service, region)):
        name = r.name.split("/")[-1]
        ready = any(c.type_ == "Ready" and str(c.state).endswith("CONDITION_SUCCEEDED")
                    for c in r.conditions)
        revs.append({"name": name, "ready": ready,
                     "traffic_percent": traffic.get(name, 0),
                     "create_time": r.create_time.isoformat() if r.create_time else None})
    revs.sort(key=lambda x: x.get("create_time") or "", reverse=True)
    return {"service": service, "revisions": revs, "uri": svc.uri}


def query_error_rate(service: str, region: str, window_minutes: int = 5) -> dict:
    """5xx count from Cloud Logging over the window. Coarse rate (0 vs >0) is enough
    for the recovery gate; refine with Monitoring request_count ratio if needed."""
    from google.cloud import logging as cloud_logging

    since = (datetime.datetime.now(datetime.timezone.utc)
             - datetime.timedelta(minutes=window_minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")
    flt = (f'resource.type="cloud_run_revision" '
           f'resource.labels.service_name="{service}" '
           f'httpRequest.status>=500 timestamp>="{since}"')
    client = cloud_logging.Client(project=config.GCP_PROJECT)
    errs = sum(1 for _ in client.list_entries(filter_=flt, max_results=200))
    return {"service": service, "error_rate": 1.0 if errs else 0.0, "errors": errs,
            "total_requests": None, "window_minutes": window_minutes}


def synthetic_probe(service: str, region: str = "", path: str = "/healthz") -> dict:
    try:
        uri = _get_service(service, config.GCP_REGION).uri
        with httpx.Client(timeout=5.0) as c:
            r = c.get(uri.rstrip("/") + path)
        return {"ok": r.status_code == 200, "path": path, "status": r.status_code}
    except Exception as e:
        return {"ok": False, "path": path, "status": 0, "error": str(e)}


def rollback_traffic_to_revision(service: str, region: str, revision: str) -> dict:
    from google.cloud import run_v2

    svc = _get_service(service, region)
    svc.traffic = [run_v2.TrafficTarget(
        type_=run_v2.TrafficTargetAllocationType.TRAFFIC_TARGET_ALLOCATION_TYPE_REVISION,
        revision=revision, percent=100)]
    op = run_v2.ServicesClient().update_service(
        service=svc, update_mask={"paths": ["traffic"]})
    op.result()  # block on the long-running operation
    return {"status": "success", "service": service, "active_revision": revision}


def restore_traffic_to_latest(service: str, region: str) -> dict:
    from google.cloud import run_v2

    svc = _get_service(service, region)
    svc.traffic = [run_v2.TrafficTarget(
        type_=run_v2.TrafficTargetAllocationType.TRAFFIC_TARGET_ALLOCATION_TYPE_LATEST,
        percent=100)]
    run_v2.ServicesClient().update_service(
        service=svc, update_mask={"paths": ["traffic"]}).result()
    return {"status": "success", "service": service, "active_revision": "LATEST"}
