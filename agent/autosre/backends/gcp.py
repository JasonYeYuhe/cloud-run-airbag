"""GCP backend — real Cloud Run + Cloud Logging. Behind AIRBAG_BACKEND=gcp.

Shapes hardened per code review (Codex + Gemini): real Condition.State enum compare,
short revision names, region-scoped log filter, post-rollback verify window, business-
path probe, update_mask on traffic updates. Verified end-to-end against the live project
(airbag-hack-260628 / asia-northeast1): detect → rollback → prove recovery → fix PR →
verify-and-undo, all on real Cloud Run.
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
    # Cloud Logging ingestion lags ~10-20s. For live detection (triage, since_epoch=None) don't
    # miss a current outage that hasn't been ingested yet — actively sample the business path.
    # The post-rollback verify (since_epoch set) stays log-based so "recovery" is proven by logs.
    if errs == 0 and since_epoch is None:
        s_errs, s_total = _active_sample(service)
        if s_errs:
            return {"service": service, "error_rate": round(s_errs / s_total, 3),
                    "errors": s_errs, "total_requests": s_total,
                    "window_minutes": window_minutes, "source": "active-probe"}
    return {"service": service, "error_rate": 1.0 if errs else 0.0,
            "errors": errs, "total_requests": None, "window_minutes": window_minutes,
            "source": "cloud-logging"}


def _active_sample(service: str, n: int = 8) -> tuple[int, int]:
    """Hit the live business path n times, counting 5xx (incl. connection failures)."""
    try:
        uri = _get_service(service, config.GCP_REGION).uri.rstrip("/") + config.PROBE_PATH
    except Exception:  # noqa: BLE001
        return 0, 0
    errs = 0
    with httpx.Client(timeout=5.0) as c:
        for _ in range(n):
            try:
                if c.get(uri).status_code >= 500:
                    errs += 1
            except Exception:  # noqa: BLE001
                errs += 1
    return errs, n


def fetch_error_logs(service: str, region: str, n: int = 10) -> list[str]:
    """Recent ERROR-severity log entries (Cloud Run logs unhandled exceptions + tracebacks to
    stderr at severity=ERROR) — the real stack trace the RCA agent reads."""
    import json as _json

    from google.cloud import logging as cloud_logging

    start = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=15))
    flt = (f'resource.type="cloud_run_revision" '
           f'resource.labels.service_name="{service}" '
           f'resource.labels.location="{region}" severity>=ERROR '
           f'timestamp>="{start.strftime("%Y-%m-%dT%H:%M:%SZ")}"')
    client = cloud_logging.Client(project=config.GCP_PROJECT)
    out: list[str] = []
    try:
        for e in client.list_entries(filter_=flt, resource_names=[f"projects/{config.GCP_PROJECT}"],
                                     order_by=cloud_logging.DESCENDING, max_results=n):
            p = getattr(e, "payload", None)
            txt = p if isinstance(p, str) else _json.dumps(p, default=str)
            if txt:
                out.append(txt[:2000])
    except Exception as ex:  # noqa: BLE001
        out.append(f"(log fetch failed: {ex})")
    return out


def synthetic_probe(service: str, path: str | None = None) -> dict:
    path = path or config.PROBE_PATH
    try:
        uri = _get_service(service, config.GCP_REGION).uri
        with httpx.Client(timeout=5.0) as c:
            r = c.get(uri.rstrip("/") + path)
        return {"ok": r.status_code == 200, "path": path, "status": r.status_code}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "path": path, "status": 0, "error": str(e)}


def _set_traffic(service: str, region: str, targets):
    from google.cloud import run_v2
    svc = _get_service(service, region)
    svc.traffic = targets if isinstance(targets, list) else [targets]
    run_v2.ServicesClient().update_service(
        service=svc, update_mask={"paths": ["traffic"]}).result(timeout=120)


def rollback_traffic_to_revision(service: str, region: str, revision: str) -> dict:
    from google.cloud import run_v2
    _set_traffic(service, region, run_v2.TrafficTarget(
        type_=run_v2.TrafficTargetAllocationType.TRAFFIC_TARGET_ALLOCATION_TYPE_REVISION,
        revision=revision, percent=100))
    return {"status": "success", "service": service, "active_revision": revision}


_CANARY_TAG = "airbagfix"


def set_traffic_split(service: str, region: str, splits: dict, tag_revision: str | None = None) -> dict:
    """Split 100% of traffic across explicit revisions, e.g. {fix: 10, safe: 90} for a canary.
    Optionally TAG one revision so it gets a stable per-revision URL we can probe directly
    (so the canary gate verifies the fix itself, not the load-balanced service)."""
    from google.cloud import run_v2
    targets = []
    for rev, pct in splits.items():
        if int(pct) <= 0:
            continue
        t = run_v2.TrafficTarget(
            type_=run_v2.TrafficTargetAllocationType.TRAFFIC_TARGET_ALLOCATION_TYPE_REVISION,
            revision=rev, percent=int(pct))
        if rev == tag_revision:
            t.tag = _CANARY_TAG
        targets.append(t)
    _set_traffic(service, region, targets)
    return {"status": "success", "service": service,
            "traffic": {r: int(p) for r, p in splits.items() if int(p) > 0}}


def probe_candidate(service: str, region: str, revision: str, n: int = 6) -> dict:
    """Probe the candidate revision DIRECTLY via its per-revision tag URL (not the load-balanced
    service URL) — so a bad fix is caught even at a low canary percentage, where the service URL
    would almost always route to the healthy revision instead."""
    try:
        svc = _get_service(service, region)
        uri = next((t.uri for t in svc.traffic_statuses
                    if getattr(t, "uri", "") and (getattr(t, "tag", "") == _CANARY_TAG
                                                  or _short(getattr(t, "revision", "")) == revision)), None)
        uri = uri or svc.uri  # last resort: load-balanced service URL
        full = uri.rstrip("/") + config.PROBE_PATH
        errs = 0
        with httpx.Client(timeout=5.0) as c:
            for _ in range(n):
                try:
                    if c.get(full).status_code >= 500:
                        errs += 1
                except Exception:  # noqa: BLE001
                    errs += 1
        return {"ok": errs == 0, "errors": errs, "total": n, "url": uri}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "errors": n, "total": n, "error": str(e)}


# --- demo harness: drive break/reset by routing traffic between revisions -----------
_FAULT_VALUES = {"bug", "http500", "delay_bomb"}


def _revision_env(rev) -> dict:
    """FAULT_MODE etc. baked into a revision's container env (skips secret refs)."""
    env: dict = {}
    for c in getattr(rev, "containers", []) or []:
        for e in getattr(c, "env", []) or []:
            if e.name:
                env[e.name] = e.value
    return env


def _ready_revisions_newest_first(service: str, region: str):
    from google.cloud import run_v2
    revs = list(run_v2.RevisionsClient().list_revisions(parent=_service_path(service, region)))
    ready = [r for r in revs if any(
        c.type_ == "Ready" and c.state == run_v2.Condition.State.CONDITION_SUCCEEDED
        for c in r.conditions)]
    ready.sort(key=lambda r: r.create_time.isoformat() if r.create_time else "", reverse=True)
    return ready


def break_target(service: str, region: str) -> dict:
    """Route 100% traffic to the bad revision. Prefer FAULT_MODE=bug (the KeyError the
    fix-PR repairs) over any other fault revision, so the demo is the unified fault."""
    ready = _ready_revisions_newest_first(service, region)
    bug = next((r for r in ready if _revision_env(r).get("FAULT_MODE") == "bug"), None)
    bad = bug or next((r for r in ready if _revision_env(r).get("FAULT_MODE") in _FAULT_VALUES), None)
    if not bad:
        return {"status": "error", "service": service,
                "error": "no fault-carrying revision found; deploy one with "
                         "FAULT_MODE=bug --no-traffic (see deploy.sh / scripts/gcp-demo.sh)"}
    name = _short(bad.name)
    rollback_traffic_to_revision(service, region, name)
    return {"status": "success", "service": service, "active_revision": name,
            "fault": _revision_env(bad).get("FAULT_MODE")}


def reset_target(service: str, region: str) -> dict:
    """Route 100% traffic to the newest healthy (no-fault) revision."""
    ready = _ready_revisions_newest_first(service, region)
    good = next((r for r in ready if _revision_env(r).get("FAULT_MODE") not in _FAULT_VALUES), None)
    if not good:
        return {"status": "error", "service": service, "error": "no healthy revision found"}
    name = _short(good.name)
    rollback_traffic_to_revision(service, region, name)
    return {"status": "success", "service": service, "active_revision": name}
