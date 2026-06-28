"""GCP backend — real Cloud Run + Cloud Logging. Behind AIRBAG_BACKEND=gcp.

Shapes hardened per code review (Codex + Gemini): real Condition.State enum compare,
short revision names, region-scoped log filter, post-rollback verify window, business-
path probe, update_mask on traffic updates. Still validate against a live project
before the demo (docs/PLAN.md) — this path is untested until `gcloud auth` exists.
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


def _short(name: str | None) -> str | None:
    return name.split("/")[-1] if name else name


def list_cloud_run_revisions(service: str, region: str) -> dict:
    from google.cloud import run_v2

    svc = _get_service(service, region)
    # Traffic can be pinned to an explicit revision OR to LATEST (revision='' on the
    # status). LATEST means "the newest revision", so resolve it after sorting.
    explicit: dict = {}
    latest_percent = 0
    for t in svc.traffic_statuses:
        if t.revision:
            explicit[_short(t.revision)] = t.percent
        elif t.type_ == run_v2.TrafficTargetAllocationType.TRAFFIC_TARGET_ALLOCATION_TYPE_LATEST:
            latest_percent += t.percent
    revs = []
    for r in run_v2.RevisionsClient().list_revisions(parent=_service_path(service, region)):
        name = _short(r.name)
        ready = any(c.type_ == "Ready" and c.state == run_v2.Condition.State.CONDITION_SUCCEEDED
                    for c in r.conditions)
        revs.append({"name": name, "ready": ready,
                     "traffic_percent": explicit.get(name, 0),
                     "create_time": r.create_time.isoformat() if r.create_time else None})
    revs.sort(key=lambda x: x.get("create_time") or "", reverse=True)
    if latest_percent and revs:
        revs[0]["traffic_percent"] += latest_percent  # LATEST == newest revision
    return {"service": service, "revisions": revs, "uri": svc.uri}


def query_error_rate(service: str, region: str, window_minutes: int = 5,
                     since_epoch: float | None = None) -> dict:
    """5xx presence from Cloud Logging. `since_epoch` (set during verify) anchors the
    window at rollback time so we don't keep counting pre-rollback errors. Coarse
    has-errors gate is enough for recovery; the synthetic probe is the active signal."""
    from google.cloud import logging as cloud_logging

    if since_epoch:
        start = datetime.datetime.fromtimestamp(since_epoch, datetime.timezone.utc)
    else:
        start = (datetime.datetime.now(datetime.timezone.utc)
                 - datetime.timedelta(minutes=window_minutes))
    flt = (f'resource.type="cloud_run_revision" '
           f'resource.labels.service_name="{service}" '
           f'resource.labels.location="{region}" '
           f'httpRequest.status>=500 timestamp>="{start.strftime("%Y-%m-%dT%H:%M:%SZ")}"')
    client = cloud_logging.Client(project=config.GCP_PROJECT)
    errs = sum(1 for _ in client.list_entries(
        filter_=flt, resource_names=[f"projects/{config.GCP_PROJECT}"], max_results=50))
    return {"service": service, "error_rate": 1.0 if errs else 0.0,
            "errors": errs, "total_requests": None, "window_minutes": window_minutes}


def synthetic_probe(service: str, path: str | None = None) -> dict:
    path = path or config.PROBE_PATH
    try:
        uri = _get_service(service, config.GCP_REGION).uri
        with httpx.Client(timeout=5.0) as c:
            r = c.get(uri.rstrip("/") + path)
        return {"ok": r.status_code == 200, "path": path, "status": r.status_code}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "path": path, "status": 0, "error": str(e)}


def _set_traffic(service: str, region: str, target):
    from google.cloud import run_v2
    svc = _get_service(service, region)
    svc.traffic = [target]
    run_v2.ServicesClient().update_service(
        service=svc, update_mask={"paths": ["traffic"]}).result(timeout=120)


def rollback_traffic_to_revision(service: str, region: str, revision: str) -> dict:
    from google.cloud import run_v2
    _set_traffic(service, region, run_v2.TrafficTarget(
        type_=run_v2.TrafficTargetAllocationType.TRAFFIC_TARGET_ALLOCATION_TYPE_REVISION,
        revision=revision, percent=100))
    return {"status": "success", "service": service, "active_revision": revision}


def restore_traffic_to_latest(service: str, region: str) -> dict:
    from google.cloud import run_v2
    _set_traffic(service, region, run_v2.TrafficTarget(
        type_=run_v2.TrafficTargetAllocationType.TRAFFIC_TARGET_ALLOCATION_TYPE_LATEST,
        percent=100))
    return {"status": "success", "service": service, "active_revision": "LATEST"}
